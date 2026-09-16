// Task 7.4 — B1, the native engine benchmark.
//
// B1 is one of three separately-labelled numbers (Open Issue 012 §3): pure C++, no Python, no
// network, no Redis. It measures matching throughput and per-order cost and nothing else. It is
// expected to come out roughly 25x B2, and *that gap is the report's whole performance story*,
// so this harness must not be contaminated by any I/O that would narrow it dishonestly.
//
// Three rules inherited from Open Issue 012 §2, which apply to a single-process benchmark just
// as much as to the open-loop one:
//
//   - Distributions, never averages. Every order's cost is recorded and reported as
//     p50/p95/p99/p99.9/max. A mean would hide exactly the allocation and rebalance spikes that
//     make the difference between 1 us and 20 us.
//   - Warm-up is discarded. The first orders pay for cold pages and a cold allocator, and on an
//     empty book they also do less work than they ever will again.
//   - The clock is measured too. Matching is expected at ~1 us and steady_clock::now() costs
//     tens of nanoseconds, so the timer is a few percent of the quantity being timed. The
//     overhead is measured and printed rather than assumed negligible.
//
// Three workloads, because "orders per second" is meaningless without saying which orders:
//
//   rest   Nothing crosses. Pure book insertion into a growing std::map. The cost that scales
//          with book depth.
//   cross  Every order crosses the resting side immediately. The matching path, one fill each.
//   mixed  A spread around a moving mid, most orders resting and some crossing. The closest
//          thing here to the shape the market bots actually produce.
//
// The order stream is generated from a fixed-seed mt19937_64. The *engine* is required to be
// deterministic (Open Issue 001) and this harness does not weaken that: the seed is fixed, so
// the same build replays the same order sequence and two runs are comparable.
//
// Build with the Dockerfile's exact flags so the numbers describe the shipped binary:
//
//     g++ -std=c++20 -O2 -Wall -Wextra -Werror -I.
//         -o bench_order_book engine/cpp/order_book.cpp engine/cpp/bench_order_book.cpp
//     ./bench_order_book --orders 200000 --json

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <random>
#include <string>
#include <vector>

#include "engine/cpp/order_book.hpp"

namespace {

using namespace quant_arena::engine;
using Clock = std::chrono::steady_clock;

constexpr const char* kSymbol = "BENCH";

struct Spec {
    long long order_id;
    long long user_id;
    Side side;
    long long price;
    long long quantity;
};

// ---------------------------------------------------------------------------------------------
// Distribution. Percentiles by nearest-rank on a sorted copy — no interpolation, so every
// number printed is a latency that actually occurred.
// ---------------------------------------------------------------------------------------------
struct Distribution {
    std::size_t count{0};
    double p50{0}, p95{0}, p99{0}, p999{0}, max{0}, min{0};

    static Distribution of(std::vector<double> samples) {
        Distribution d;
        if (samples.empty()) {
            return d;
        }
        std::sort(samples.begin(), samples.end());
        d.count = samples.size();
        d.min = samples.front();
        d.max = samples.back();
        d.p50 = at(samples, 0.50);
        d.p95 = at(samples, 0.95);
        d.p99 = at(samples, 0.99);
        d.p999 = at(samples, 0.999);
        return d;
    }

private:
    static double at(const std::vector<double>& sorted, double q) {
        const auto n = sorted.size();
        auto rank = static_cast<std::size_t>(q * static_cast<double>(n));
        if (rank >= n) {
            rank = n - 1;
        }
        return sorted[rank];
    }
};

// ---------------------------------------------------------------------------------------------
// Workload generation.
// ---------------------------------------------------------------------------------------------
enum class Workload { Rest, Cross, Mixed };

const char* workload_name(Workload w) {
    switch (w) {
        case Workload::Rest:
            return "rest";
        case Workload::Cross:
            return "cross";
        case Workload::Mixed:
            return "mixed";
    }
    return "unknown";
}

// `rest`: bids strictly below asks forever, so the book only ever grows. Prices walk outward
// from the mid so that no two orders collide into the same level too often — a book of 200,000
// orders on four levels would measure the deque, not the map.
std::vector<Spec> generate_rest(std::size_t n, std::uint64_t seed) {
    std::mt19937_64 rng(seed);
    std::vector<Spec> out;
    out.reserve(n);
    for (std::size_t i = 0; i < n; ++i) {
        const bool buy = (rng() & 1u) == 0;
        const auto offset = static_cast<long long>(rng() % 5000);
        out.push_back(Spec{
            static_cast<long long>(i + 1),
            static_cast<long long>(i % 64) + 1,
            buy ? Side::Buy : Side::Sell,
            buy ? 100000 - 1 - offset : 100000 + 1 + offset,
            1 + static_cast<long long>(rng() % 50),
        });
    }
    return out;
}

// `cross`: alternate sides at one price. Every second order takes the one before it, so the book
// stays near-empty and the measurement is the matching path rather than map growth.
std::vector<Spec> generate_cross(std::size_t n, std::uint64_t seed) {
    std::mt19937_64 rng(seed);
    std::vector<Spec> out;
    out.reserve(n);
    for (std::size_t i = 0; i < n; ++i) {
        const bool buy = (i % 2) == 0;
        out.push_back(Spec{
            static_cast<long long>(i + 1),
            static_cast<long long>(i % 64) + 1,
            buy ? Side::Buy : Side::Sell,
            100000,
            1 + static_cast<long long>(rng() % 10),
        });
    }
    return out;
}

// `mixed`: a mid that random-walks, a two-tick spread, and roughly one order in eight priced
// through the spread so it crosses. Resting orders cluster within ten ticks of the mid, which is
// what the market-maker bot does.
std::vector<Spec> generate_mixed(std::size_t n, std::uint64_t seed) {
    std::mt19937_64 rng(seed);
    std::vector<Spec> out;
    out.reserve(n);
    long long mid = 100000;
    for (std::size_t i = 0; i < n; ++i) {
        if ((rng() % 32) == 0) {
            mid += (rng() & 1u) ? 1 : -1;
        }
        const bool buy = (rng() & 1u) == 0;
        const bool aggressive = (rng() % 8) == 0;
        long long price;
        if (aggressive) {
            price = buy ? mid + 2 + static_cast<long long>(rng() % 3)
                        : mid - 2 - static_cast<long long>(rng() % 3);
        } else {
            price = buy ? mid - 1 - static_cast<long long>(rng() % 10)
                        : mid + 1 + static_cast<long long>(rng() % 10);
        }
        out.push_back(Spec{
            static_cast<long long>(i + 1),
            static_cast<long long>(i % 64) + 1,
            buy ? Side::Buy : Side::Sell,
            price,
            1 + static_cast<long long>(rng() % 50),
        });
    }
    return out;
}

std::vector<Spec> generate(Workload w, std::size_t n, std::uint64_t seed) {
    switch (w) {
        case Workload::Rest:
            return generate_rest(n, seed);
        case Workload::Cross:
            return generate_cross(n, seed);
        case Workload::Mixed:
            return generate_mixed(n, seed);
    }
    return {};
}

// ---------------------------------------------------------------------------------------------
// Clock overhead. Two back-to-back now() calls, many times over, distribution reported. This is
// subtracted from nothing — it is printed so the reader can judge the per-order numbers against
// the cost of having measured them.
// ---------------------------------------------------------------------------------------------
Distribution measure_clock_overhead(std::size_t n) {
    std::vector<double> samples;
    samples.reserve(n);
    for (std::size_t i = 0; i < n; ++i) {
        const auto a = Clock::now();
        const auto b = Clock::now();
        samples.push_back(
            static_cast<double>(std::chrono::duration_cast<std::chrono::nanoseconds>(b - a).count()));
    }
    return Distribution::of(std::move(samples));
}

struct Result {
    Workload workload;
    std::size_t orders_measured{0};
    std::size_t warmup{0};
    std::size_t fills{0};
    std::size_t resting_at_end{0};
    double wall_seconds{0};
    double orders_per_second{0};
    Distribution per_order_ns;
};

// One run. Every order goes through add_order_and_match, which is the engine's real entry point:
// it returns a std::vector<Fill> per call, so the allocation that implies is inside the measured
// window on purpose. Hiding it would flatter the engine and mislead the reader.
Result run(Workload w, std::size_t orders, std::size_t warmup, std::uint64_t seed) {
    const auto specs = generate(w, orders + warmup, seed);
    OrderBook book;
    std::vector<double> samples;
    samples.reserve(orders);

    std::size_t fills = 0;
    long long fake_clock = 0;
    double wall_ns = 0;

    for (std::size_t i = 0; i < specs.size(); ++i) {
        const auto& s = specs[i];
        const Order order(s.order_id, s.user_id, kSymbol, s.side, s.price, s.quantity, ++fake_clock);

        const auto start = Clock::now();
        const auto produced = book.add_order_and_match(order);
        const auto stop = Clock::now();

        const auto ns =
            static_cast<double>(std::chrono::duration_cast<std::chrono::nanoseconds>(stop - start).count());
        if (i >= warmup) {
            samples.push_back(ns);
            wall_ns += ns;
            fills += produced.size();
        }
    }

    Result r;
    r.workload = w;
    r.orders_measured = samples.size();
    r.warmup = warmup;
    r.fills = fills;
    r.resting_at_end = 0;
    r.wall_seconds = wall_ns / 1e9;
    r.orders_per_second = r.wall_seconds > 0 ? static_cast<double>(r.orders_measured) / r.wall_seconds : 0;
    r.per_order_ns = Distribution::of(std::move(samples));
    return r;
}

void print_table(const std::vector<Result>& results, const Distribution& clock_overhead) {
    std::cout << "\nB1 — native engine, per-order cost in nanoseconds\n";
    std::cout << "clock overhead (two back-to-back now() calls): p50 " << clock_overhead.p50
              << " ns, p99 " << clock_overhead.p99 << " ns, max " << clock_overhead.max << " ns\n\n";
    std::cout << std::left << std::setw(8) << "load" << std::right << std::setw(11) << "orders"
              << std::setw(10) << "fills" << std::setw(9) << "p50" << std::setw(9) << "p95"
              << std::setw(9) << "p99" << std::setw(10) << "p99.9" << std::setw(11) << "max"
              << std::setw(14) << "orders/sec" << "\n";
    std::cout << std::string(91, '-') << "\n";
    // Fixed, zero-decimal for every numeric column. Set once: switching back to defaultfloat
    // mid-row printed three-digit nanosecond figures as "2e+02" in the first version of this.
    std::cout << std::fixed << std::setprecision(0);
    for (const auto& r : results) {
        std::cout << std::left << std::setw(8) << workload_name(r.workload) << std::right
                  << std::setw(11) << r.orders_measured << std::setw(10) << r.fills << std::setw(9)
                  << r.per_order_ns.p50 << std::setw(9) << r.per_order_ns.p95 << std::setw(9)
                  << r.per_order_ns.p99 << std::setw(10) << r.per_order_ns.p999 << std::setw(11)
                  << r.per_order_ns.max << std::setw(14) << r.orders_per_second << "\n";
    }
    std::cout << "\nWarm-up discarded: " << (results.empty() ? 0 : results.front().warmup)
              << " orders per workload.\n";
}

void print_json(const std::vector<Result>& results, const Distribution& clock_overhead,
                std::uint64_t seed) {
    auto dist = [](const Distribution& d) {
        std::cout << "{\"count\": " << d.count << ", \"min\": " << d.min << ", \"p50\": " << d.p50
                  << ", \"p95\": " << d.p95 << ", \"p99\": " << d.p99 << ", \"p99_9\": " << d.p999
                  << ", \"max\": " << d.max << "}";
    };
    std::cout << "{\"benchmark\": \"B1\", \"seed\": " << seed << ", \"clock_overhead_ns\": ";
    dist(clock_overhead);
    std::cout << ", \"workloads\": [";
    for (std::size_t i = 0; i < results.size(); ++i) {
        const auto& r = results[i];
        if (i) {
            std::cout << ", ";
        }
        std::cout << "{\"workload\": \"" << workload_name(r.workload) << "\", \"orders\": "
                  << r.orders_measured << ", \"warmup\": " << r.warmup << ", \"fills\": " << r.fills
                  << ", \"seconds\": " << r.wall_seconds
                  << ", \"orders_per_second\": " << r.orders_per_second << ", \"per_order_ns\": ";
        dist(r.per_order_ns);
        std::cout << "}";
    }
    std::cout << "]}\n";
}

}  // namespace

int main(int argc, char** argv) {
    std::size_t orders = 200000;
    std::size_t warmup = 20000;
    std::uint64_t seed = 20260916;
    bool as_json = false;

    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        const auto value = [&]() -> std::string { return (i + 1 < argc) ? argv[++i] : ""; };
        if (arg == "--orders") {
            orders = std::strtoull(value().c_str(), nullptr, 10);
        } else if (arg == "--warmup") {
            warmup = std::strtoull(value().c_str(), nullptr, 10);
        } else if (arg == "--seed") {
            seed = std::strtoull(value().c_str(), nullptr, 10);
        } else if (arg == "--json") {
            as_json = true;
        } else if (arg == "--help") {
            std::cout << "usage: bench_order_book [--orders N] [--warmup N] [--seed N] [--json]\n";
            return 0;
        } else {
            std::cerr << "unknown argument: " << arg << "\n";
            return 2;
        }
    }

    if (orders == 0) {
        std::cerr << "--orders must be positive\n";
        return 2;
    }

    const auto clock_overhead = measure_clock_overhead(100000);

    std::vector<Result> results;
    for (const auto w : {Workload::Rest, Workload::Cross, Workload::Mixed}) {
        results.push_back(run(w, orders, warmup, seed));
    }

    if (as_json) {
        print_json(results, clock_overhead, seed);
    } else {
        print_table(results, clock_overhead);
    }
    return 0;
}
