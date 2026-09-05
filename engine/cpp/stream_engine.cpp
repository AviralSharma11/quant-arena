#include "order_book.hpp"

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

namespace {

using Byte = std::uint8_t;
using Bytes = std::vector<Byte>;
using quant_arena::engine::Fill;
using quant_arena::engine::Order;
using quant_arena::engine::OrderBook;
using quant_arena::engine::Side;

constexpr std::uint16_t kSchemaVersion = 1;
constexpr std::uint16_t kSubmitOrder = 1;
constexpr std::uint16_t kCancelOrder = 2;
constexpr std::uint16_t kCreateAccount = 3;
constexpr std::uint16_t kCreditCash = 4;
constexpr std::uint16_t kOrderAccepted = 10;
constexpr std::uint16_t kOrderRejected = 11;
constexpr std::uint16_t kFill = 12;
constexpr std::uint16_t kOrderCancelled = 13;
constexpr std::uint16_t kAccountCreated = 15;
constexpr std::uint16_t kCashCredited = 16;

constexpr std::uint8_t kBuy = 1;
constexpr std::uint8_t kSell = 2;
constexpr std::uint8_t kGtc = 1;
constexpr std::uint8_t kIoc = 2;
constexpr std::uint8_t kUserRequested = 1;
constexpr std::uint8_t kIocExpired = 2;
constexpr std::uint16_t kInvalidPrice = 2;
constexpr std::uint16_t kInvalidQuantity = 3;
constexpr std::uint16_t kInvalidSide = 4;
constexpr std::uint16_t kInvalidTif = 5;
constexpr std::uint16_t kUnknownOrder = 8;

std::uint16_t read_u16(const Bytes& data, std::size_t offset) {
    if (offset + 2 > data.size()) {
        throw std::runtime_error("record is shorter than its field offset");
    }
    return static_cast<std::uint16_t>(data[offset]) |
           (static_cast<std::uint16_t>(data[offset + 1]) << 8);
}

std::uint64_t read_u64(const Bytes& data, std::size_t offset) {
    if (offset + 8 > data.size()) {
        throw std::runtime_error("record is shorter than its field offset");
    }
    std::uint64_t value = 0;
    for (unsigned int index = 0; index < 8; ++index) {
        value |= static_cast<std::uint64_t>(data[offset + index]) << (index * 8);
    }
    return value;
}

std::int64_t read_i64(const Bytes& data, std::size_t offset) {
    return static_cast<std::int64_t>(read_u64(data, offset));
}

std::int16_t read_i16(const Bytes& data, std::size_t offset) {
    return static_cast<std::int16_t>(read_u16(data, offset));
}

std::uint8_t read_u8(const Bytes& data, std::size_t offset) {
    if (offset >= data.size()) {
        throw std::runtime_error("record is shorter than its field offset");
    }
    return data[offset];
}

void write_u16(Bytes& data, std::size_t offset, std::uint16_t value) {
    data[offset] = static_cast<Byte>(value & 0xff);
    data[offset + 1] = static_cast<Byte>((value >> 8) & 0xff);
}

void write_u64(Bytes& data, std::size_t offset, std::uint64_t value) {
    for (unsigned int index = 0; index < 8; ++index) {
        data[offset + index] = static_cast<Byte>((value >> (index * 8)) & 0xff);
    }
}

void write_i64(Bytes& data, std::size_t offset, std::int64_t value) {
    write_u64(data, offset, static_cast<std::uint64_t>(value));
}

Bytes record_header(std::uint16_t record_type, std::size_t size) {
    Bytes data(size, 0);
    write_u16(data, 0, kSchemaVersion);
    write_u16(data, 2, record_type);
    return data;
}

std::uint32_t read_frame_length() {
    Byte bytes[4];
    std::cin.read(reinterpret_cast<char*>(bytes), sizeof(bytes));
    if (std::cin.eof() && std::cin.gcount() == 0) {
        return 0;
    }
    if (std::cin.gcount() != 4) {
        throw std::runtime_error("truncated input frame length");
    }
    return static_cast<std::uint32_t>(bytes[0]) |
           (static_cast<std::uint32_t>(bytes[1]) << 8) |
           (static_cast<std::uint32_t>(bytes[2]) << 16) |
           (static_cast<std::uint32_t>(bytes[3]) << 24);
}

Bytes read_frame(std::uint32_t length) {
    Bytes data(length);
    if (length == 0) {
        return data;
    }
    std::cin.read(reinterpret_cast<char*>(data.data()), static_cast<std::streamsize>(length));
    if (std::cin.gcount() != static_cast<std::streamsize>(length)) {
        throw std::runtime_error("truncated input frame");
    }
    return data;
}

void write_frame(const Bytes& data) {
    const std::uint32_t length = static_cast<std::uint32_t>(data.size());
    Byte header[4]{
        static_cast<Byte>(length & 0xff),
        static_cast<Byte>((length >> 8) & 0xff),
        static_cast<Byte>((length >> 16) & 0xff),
        static_cast<Byte>((length >> 24) & 0xff),
    };
    std::cout.write(reinterpret_cast<const char*>(header), sizeof(header));
    if (!data.empty()) {
        std::cout.write(reinterpret_cast<const char*>(data.data()),
                        static_cast<std::streamsize>(data.size()));
    }
    std::cout.flush();
}

struct LiveOrder {
    std::uint64_t order_id;
    std::uint64_t client_order_id;
    std::uint64_t user_id;
    std::int16_t symbol_id;
    std::uint8_t side;
    std::int64_t price_ticks;
};

class StreamEngine {
public:
    explicit StreamEngine(std::int64_t initial_cash_ticks)
        : initial_cash_ticks_(initial_cash_ticks) {}

    std::vector<Bytes> apply(const Bytes& record) {
        const auto record_type = read_u16(record, 2);
        switch (record_type) {
        case kSubmitOrder:
            return submit(record);
        case kCancelOrder:
            return cancel(record);
        case kCreateAccount:
            return create_account(record);
        case kCreditCash:
            return credit_cash(record);
        default:
            return {};
        }
    }

private:
    using ClientKey = std::pair<std::uint64_t, std::uint64_t>;

    OrderBook& book(std::int16_t symbol_id) {
        return books_[symbol_id];
    }

    static ClientKey client_key(std::uint64_t user_id, std::uint64_t client_order_id) {
        return {user_id, client_order_id};
    }

    Bytes rejected(const Bytes& input, std::uint16_t reason) const {
        Bytes output = record_header(kOrderRejected, 48);
        write_i64(output, 20, read_i64(input, 20));
        write_u64(output, 28, read_u64(input, 28));
        write_u64(output, 36, read_u64(input, 36));
        write_u16(output, 44, static_cast<std::uint16_t>(read_i16(input, 60)));
        write_u16(output, 46, reason);
        return output;
    }

    Bytes rejected_cancel(const Bytes& input, std::uint16_t reason) const {
        Bytes output = record_header(kOrderRejected, 48);
        write_i64(output, 20, read_i64(input, 20));
        write_u64(output, 28, read_u64(input, 28));
        write_u64(output, 36, read_u64(input, 36));
        write_u16(output, 44, 0);
        write_u16(output, 46, reason);
        return output;
    }

    Bytes accepted(const Bytes& input, std::uint64_t order_id) const {
        Bytes output = record_header(kOrderAccepted, 72);
        write_i64(output, 20, read_i64(input, 20));
        write_u64(output, 28, order_id);
        write_u64(output, 36, read_u64(input, 28));
        write_u64(output, 44, read_u64(input, 36));
        write_i64(output, 52, read_i64(input, 44));
        write_i64(output, 60, read_i64(input, 52));
        write_u16(output, 68, static_cast<std::uint16_t>(read_i16(input, 60)));
        output[70] = read_u8(input, 62);
        output[71] = read_u8(input, 63);
        return output;
    }

    Bytes fill_record(const Bytes& input, const Fill& fill, const LiveOrder& maker,
                      const LiveOrder& taker) const {
        Bytes output = record_header(kFill, 79);
        write_i64(output, 20, read_i64(input, 20));
        write_u64(output, 28, maker.order_id);
        write_u64(output, 36, taker.order_id);
        write_u64(output, 44, maker.user_id);
        write_u64(output, 52, taker.user_id);
        write_i64(output, 60, fill.price);
        write_i64(output, 68, fill.quantity);
        write_u16(output, 76, static_cast<std::uint16_t>(maker.symbol_id));
        output[78] = taker.side;
        return output;
    }

    Bytes cancelled(const Bytes& input, const LiveOrder& order, std::int64_t remaining,
                    std::uint8_t reason) const {
        Bytes output = record_header(kOrderCancelled, 63);
        write_i64(output, 20, read_i64(input, 20));
        write_u64(output, 28, order.order_id);
        write_u64(output, 36, order.client_order_id);
        write_u64(output, 44, order.user_id);
        write_i64(output, 52, remaining);
        write_u16(output, 60, static_cast<std::uint16_t>(order.symbol_id));
        output[62] = reason;
        return output;
    }

    void forget_if_filled(std::int16_t symbol_id, std::uint64_t order_id) {
        if (book(symbol_id).get_order(static_cast<long long>(order_id)).has_value()) {
            return;
        }
        const auto live = live_.find(order_id);
        if (live == live_.end()) {
            return;
        }
        client_to_order_.erase(client_key(live->second.user_id, live->second.client_order_id));
        live_.erase(live);
    }

    std::vector<Bytes> submit(const Bytes& input) {
        const auto price = read_i64(input, 44);
        const auto quantity = read_i64(input, 52);
        const auto side = read_u8(input, 62);
        const auto tif = read_u8(input, 63);
        if (price <= 0) {
            return {rejected(input, kInvalidPrice)};
        }
        if (quantity <= 0) {
            return {rejected(input, kInvalidQuantity)};
        }
        if (side != kBuy && side != kSell) {
            return {rejected(input, kInvalidSide)};
        }
        if (tif != kGtc && tif != kIoc) {
            return {rejected(input, kInvalidTif)};
        }

        const auto order_id = next_order_id_++;
        const auto user_id = read_u64(input, 36);
        const auto client_order_id = read_u64(input, 28);
        const auto symbol_id = read_i16(input, 60);
        const LiveOrder live{
            order_id, client_order_id, user_id, symbol_id, side, price,
        };
        const auto key = client_key(user_id, client_order_id);
        client_to_order_[key] = order_id;
        live_[order_id] = live;

        auto& target_book = book(symbol_id);
        target_book.add_order(Order{
            static_cast<long long>(order_id),
            static_cast<long long>(user_id),
            std::to_string(symbol_id),
            side == kBuy ? Side::Buy : Side::Sell,
            static_cast<long long>(price),
            static_cast<long long>(quantity),
            read_i64(input, 20),
        });

        std::vector<Bytes> output{accepted(input, order_id)};
        const auto fills = target_book.match();
        for (const auto& fill : fills) {
            const auto buy = live_.at(static_cast<std::uint64_t>(fill.buy_order_id));
            const auto sell = live_.at(static_cast<std::uint64_t>(fill.sell_order_id));
            const auto& maker = buy.order_id == order_id ? sell : buy;
            const auto& taker = buy.order_id == order_id ? buy : sell;
            output.push_back(fill_record(input, fill, maker, taker));
        }

        for (const auto& fill : fills) {
            forget_if_filled(symbol_id, static_cast<std::uint64_t>(fill.buy_order_id));
            forget_if_filled(symbol_id, static_cast<std::uint64_t>(fill.sell_order_id));
        }

        const auto current = live_.find(order_id);
        if (tif == kIoc && current != live_.end()) {
            const auto order = target_book.get_order(static_cast<long long>(order_id));
            if (order.has_value() && order->remaining_quantity > 0) {
                const auto remaining = order->remaining_quantity;
                target_book.cancel_order(static_cast<long long>(order_id));
                output.push_back(cancelled(input, current->second, remaining, kIocExpired));
                client_to_order_.erase(key);
                live_.erase(current);
            }
        }
        return output;
    }

    std::vector<Bytes> cancel(const Bytes& input) {
        const auto user_id = read_u64(input, 36);
        const auto target_client_order_id = read_u64(input, 44);
        const auto lookup = client_to_order_.find(client_key(user_id, target_client_order_id));
        if (lookup == client_to_order_.end()) {
            return {rejected_cancel(input, kUnknownOrder)};
        }

        const auto live_it = live_.find(lookup->second);
        if (live_it == live_.end()) {
            throw std::runtime_error("client order index points to a missing live order");
        }
        const auto order = live_it->second;
        auto& target_book = book(order.symbol_id);
        const auto current = target_book.get_order(static_cast<long long>(order.order_id));
        if (!current.has_value()) {
            client_to_order_.erase(lookup);
            live_.erase(live_it);
            return {rejected_cancel(input, kUnknownOrder)};
        }
        const auto remaining = current->remaining_quantity;
        target_book.cancel_order(static_cast<long long>(order.order_id));
        client_to_order_.erase(lookup);
        live_.erase(live_it);
        return {cancelled(input, order, remaining, kUserRequested)};
    }

    std::vector<Bytes> create_account(const Bytes& input) const {
        Bytes output = record_header(kAccountCreated, 52);
        write_i64(output, 20, read_i64(input, 20));
        write_u64(output, 28, read_u64(input, 28));
        write_u64(output, 36, read_u64(input, 36));
        write_i64(output, 44, initial_cash_ticks_);
        return {output};
    }

    std::vector<Bytes> credit_cash(const Bytes& input) const {
        Bytes output = record_header(kCashCredited, 52);
        write_i64(output, 20, read_i64(input, 20));
        write_u64(output, 28, read_u64(input, 28));
        write_u64(output, 36, read_u64(input, 36));
        write_i64(output, 44, read_i64(input, 44));
        return {output};
    }

    std::int64_t initial_cash_ticks_;
    std::uint64_t next_order_id_{1};
    std::map<std::int16_t, OrderBook> books_;
    std::map<std::uint64_t, LiveOrder> live_;
    std::map<ClientKey, std::uint64_t> client_to_order_;
};

std::int64_t initial_cash_from_args(int argc, char** argv) {
    if (argc != 3 || std::string(argv[1]) != "--initial-cash-ticks") {
        throw std::runtime_error(
            "usage: quant-arena-engine --initial-cash-ticks <signed integer>");
    }
    std::size_t consumed = 0;
    const auto value = std::stoll(argv[2], &consumed);
    if (consumed != std::strlen(argv[2])) {
        throw std::runtime_error("initial cash must be an integer");
    }
    return value;
}

}  // namespace

int main(int argc, char** argv) {
    try {
#ifdef _WIN32
        _setmode(_fileno(stdin), _O_BINARY);
        _setmode(_fileno(stdout), _O_BINARY);
#endif
        StreamEngine engine(initial_cash_from_args(argc, argv));
        while (true) {
            const auto length = read_frame_length();
            if (!std::cin && length == 0) {
                break;
            }
            if (length == 0) {
                throw std::runtime_error("zero-length input records are not allowed");
            }
            const auto input = read_frame(length);
            for (const auto& output : engine.apply(input)) {
                write_frame(output);
            }
            write_frame({});
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "quant-arena-engine: " << error.what() << '\n';
        return 1;
    }
}
