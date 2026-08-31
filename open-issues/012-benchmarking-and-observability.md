# Open Issue 012 — Benchmarking and Observability

**Status:** CONFIRMED (2026-08-28) — full scope retained; logs only, no Grafana
**Opened:** 2026-08-27
**Serves:** README.md Goal 5 (measure and improve performance), Goal 6 (production practices)
**Owner:** _unassigned_

---

## 1. The problem

`README.md` Goal 5 is unusually well framed: *"We will measure performance instead of making
unsupported claims."* Success is defined as being able to publish results and explain how the
tests were performed, what bottlenecks were found, what was changed, and what effect it had.

That is a higher bar than it appears. Most projects publish a single throughput number
produced by a benchmark that is quietly measuring the wrong thing. **The difficulty is not
making the system fast; it is measuring it in a way that survives scrutiny.**

---

## 2. The methodology problem that decides whether the numbers mean anything

### Coordinated omission

The most common benchmarking error, and the one worth getting right because almost nobody
does.

A naive load generator sends a request, waits for the response, then sends the next. If the
system stalls for 200 ms, the generator also stalls — so it never issues the requests that
*would have arrived* during the stall, and never records their latency. The stall is
effectively erased from the measurement. Reported p99 looks excellent; real users experienced
something far worse.

**Proposed: open-loop load generation.** Requests are issued on a fixed schedule regardless of
whether earlier ones have completed. Latency is measured from **intended** send time, not from
actual send time. If the system falls behind, the queue grows and the measurement shows it —
which is the entire point.

This single decision is the difference between a benchmark that means something and one that
does not, and explaining it is a stronger demonstration of understanding than any number the
benchmark produces.

### Record distributions, never averages

Averages conceal exactly the behaviour that matters. Record full latency distributions using
an HDR histogram and report p50, p95, p99 and p99.9, alongside the maximum.

### Warm-up and steady state

Discard the first interval — JIT, page cache, TCP window growth, and Redis AOF behaviour all
change during it. Report only steady-state measurements, and state the discarded interval.

---

## 3. Sub-decision 12a — What is measured, and where

Three separate benchmarks producing three separately-labelled numbers. Conflating them is how
credibility is lost.

| # | Benchmark | Harness | Measures |
|---|---|---|---|
| **B1** | Native engine | Pure C++, no Python, no network | Matching throughput and per-order cost |
| **B2** | End-to-end | HTTP in → WebSocket out, full pipeline | The number that reflects user experience |
| **B3** | Fan-out | Many WebSocket clients, no order flow | Connections supported; update delay |

**B1 is expected to be roughly 25× B2**, and *that gap is the most interesting result the
report contains*, because explaining it is the whole performance story. Reporting only B1
would be dishonest; reporting only B2 would waste the engine work.

### The latency budget

Expected end-to-end breakdown, to be replaced with measurements:

| Stage | Expected |
|---|---|
| HTTP parse, auth, validation | ~200 µs |
| Risk check and reservation (in-memory) | ~10 µs |
| `XADD` to inbound stream, including durability | ~100 µs |
| Engine `XREAD` and dispatch | ~50 µs |
| **Matching** | **~1 µs** |
| `XADD` to outbound stream | ~100 µs |
| Fan-out read, conflation wait (20 Hz) | **up to 50 ms** |
| WebSocket write | ~50 µs |

Two things fall out immediately, and both are worth stating in the report because they are
counter-intuitive:

1. **The matching engine is roughly 0.002% of end-to-end latency.** The component optimised
   hardest is the one that matters least to the user — an honest and instructive result.
2. **Conflation dominates everything else by three orders of magnitude.** The 20 Hz choice in
   Open Issue 006 is, by a wide margin, the largest single latency contributor, and it was
   chosen deliberately for demo feel. That is a legitimate trade, but it must be stated
   rather than hidden.

---

## 4. Sub-decision 12b — The metrics that README.md names

Goal 5 lists: orders per second, average latency, p50, p95, p99, connected users, real-time
update delay, and recovery time after failure.

| Metric | How it is obtained |
|---|---|
| Orders/sec | B1 and B2, reported separately |
| p50 / p95 / p99 latency | HDR histogram from open-loop generation |
| Connected users | B3, increased until update delay degrades |
| Real-time update delay | Timestamp at engine output versus arrival at a client |
| **Recovery time** | The kill-the-engine script from Open Issue 007, timed |
| **Market quality under load** | Spread, depth, and market-maker obligation compliance (Open Issue 005 §5h) |

The last row is not in `README.md` and is proposed as an addition. **Watching the spread
widen and market-maker obligations start to breach as load increases is a more interesting
result than throughput**, and it is a genuinely domain-specific measurement that a generic
web-service benchmark cannot produce.

---

## 5. Sub-decision 12c — Observability

### Structured logging

JSON logs from every process, each carrying `client_order_id`, `order_id`, and stream
sequence number where applicable. Roughly 4 hours.

### Metrics

**Proposed: Prometheus plus Grafana**, both in the `docker-compose` stack. Approximately 8
hours including dashboards. This is justified rather than decorative: the benchmark work needs
time-series visibility into queue depth, stream lag, and latency percentiles, and reading
those from logs is impractical.

Core series: inbound stream lag per consumer, orders/sec, latency percentiles, WebSocket
connection count, per-client send-buffer depth, Redis memory and AOF state, market-maker
obligation compliance.

### Tracing — and why OpenTelemetry is not proposed

`README.md` Goal 6 lists distributed tracing. The conventional answer is OpenTelemetry with
a collector and a backend such as Jaeger, at a cost of roughly 15 hours plus operational
weight.

**That is unnecessary here, because the event stream already is the trace.**

Every request carries a `client_order_id` (Open Issue 008) and receives a stream sequence
number. Every downstream event references them. Reconstructing the complete path of an order —
gateway to engine to ledger to fan-out, with timing at each hop — is a query over the stream,
not a separate tracing system.

**Proposed:** build a small `trace <client_order_id>` tool that reconstructs and prints an
order's full path with per-hop timings. Roughly 4 hours against approximately 15, and it is
*better* than OpenTelemetry for this system because it traces the actual ordered record rather
than sampled spans.

This is worth stating explicitly in the README: **the tracing requirement is met by the
architecture rather than by adding a tracing product.** Adding OpenTelemetry alongside would
be precisely the resume-driven architecture that `README.md` §5 rejects.

---

## 6. Sub-decision 12d — The report is a deliverable

For audience (a), the benchmark report is read more often than the code. It must contain:

1. **Methodology** — open-loop generation, coordinated omission and how it was avoided,
   warm-up policy, hardware specification, software versions, configuration content hash.
2. **Results** — B1, B2 and B3 separately labelled, with full distributions rather than
   averages.
3. **The latency budget**, measured, with the two counter-intuitive findings from §3.
4. **Bottleneck analysis** — what saturated first, and how that was established.
5. **At least one before/after** — a change made, with numbers on both sides.
6. **Limitations** — what was not tested, and what the numbers do not support.

Section 6 matters more than it appears. A report that states its own limits is far more
credible than one that does not, and every informed reader looks for it.

---

## 7. Cost summary

| Item | Hours |
|---|---|
| Open-loop load generator with HDR histograms | 10 |
| Native engine benchmark harness (B1) | 4 |
| Fan-out benchmark harness (B3) | 4 |
| Prometheus, Grafana, dashboards | 8 |
| Structured logging with correlation identifiers | 4 |
| `trace <client_order_id>` tool | 4 |
| Writing the report | 8 |
| **Total** | **42** |

Against **20 hours** budgeted for "benchmark harness and report". The gap is roughly **22
hours** — the largest single overrun identified so far.

**Where it could be cut:** drop Prometheus and Grafana (−8 h) and rely on logs plus the load
generator's own output. That saves the most hours but removes the visibility the optimisation
work depends on, which risks costing more than it saves.

---

## 8. Questions to resolve

1. **Is the 22-hour overrun acceptable?** Combined with the accepted increases for testing and
   backtesting, the cumulative slack is materially reduced — see the running tally.
2. **Prometheus and Grafana, or logs only?** Grafana is what makes the optimisation work
   tractable, and dashboard screenshots are strong material for the report.
3. **Is the `trace` tool accepted in place of OpenTelemetry?** It is cheaper and better suited,
   but "we used OpenTelemetry" is a more familiar phrase to some readers than "our event
   stream is the trace."
4. **Should market quality under load (§4) be a headline metric?** It is domain-specific and
   more interesting than throughput, but it is not in `README.md`'s list.

---

## 9. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Sub-decisions 12a–12d proposed; coordinated omission identified as the central methodology risk; 22 h overrun identified. Nothing final. |

---

## 10. Amendment 2026-08-27 — answers recorded

- **§8.1 — full scope retained; the schedule risk is accepted deliberately.** No feature cuts.
- **§8.2 — Prometheus and Grafana are dropped. Logs only.**
- No separate budget-tracking document will be maintained.

### 12e — What replaces Grafana

Dropping Grafana removes the visibility the optimisation work depends on, so it is replaced
rather than simply deleted. Two cheap substitutes cover the two distinct needs:

| Need | Substitute | Hours |
|---|---|---|
| **Live visibility during a load run** — watching queue depth grow, spotting the moment throughput collapses | The load generator prints a summary line each second: offered rate, achieved rate, p50/p99, stream lag | 1 |
| **Analysis and report artefacts** | An offline script over the archived event stream and structured logs, producing latency distributions, throughput over time, and queue depth, plotted to PNG | 4 |

**The offline route is arguably better suited to this project than Grafana was.** Audience (a)
reads the benchmark report; they never see a live dashboard. Plots generated directly from the
archived stream go straight into the report, and — because the stream carries a timestamp at
every hop — they can be regenerated from any recorded run rather than depending on metrics
having been scraped while it happened.

**Honest accounting:** dropping Grafana saves 8 hours; the substitutes cost 5. **Net saving is
3 hours, not 8.**

### Revised total

| | Hours |
|---|---|
| Running total at §8 | 424 |
| Grafana removed, substitutes added | −3 |
| **Revised** | **421** |
| Effective budget | ~430 |

Roughly **9 hours of slack**, against estimates that historically run 30–50% over for
unfamiliar work. The overrun risk is accepted as a deliberate decision, recorded here so it is
not later mistaken for an oversight.

## 11. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Sub-decisions 12a–12d proposed; coordinated omission identified; 22 h overrun surfaced |
| 2026-08-27 | AMENDED | Full scope retained with schedule risk accepted; Grafana dropped in favour of logs plus offline analysis (12e). Still not final. |

---

## Amendment 2026-08-28 — simplification pass (Open Issue 018)

The **B3 fan-out benchmark is folded into B2** rather than built as a separate harness (−4 h). B1 and B2 continue to be measured and reported separately, which is the split that matters.
