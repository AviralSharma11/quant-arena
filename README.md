# Quant Arena

## Running it

Requires Docker Desktop. Nothing else — no Python, no Redis, no PostgreSQL on the host.

```bash
git clone https://github.com/AviralSharma11/quant-arena.git
cd quant-arena
docker compose up --build
```

That brings up Redis, PostgreSQL and the gateway, in that order, each waiting for the one
before it to report healthy. The API is then at `http://localhost:8000`, with interactive
documentation at `http://localhost:8000/docs`.

```bash
docker compose ps          # health of each service
docker compose logs gateway
docker compose down        # stop;  add -v to also delete the database volume
```

### Checking that it came up correctly

```bash
curl http://localhost:8000/health
docker compose logs gateway | grep config_hash
```

The second command is the interesting one. Every process prints a single JSON line at startup
carrying the SHA-256 of the configuration it just read:

```json
{"event":"startup","process":"gateway","config_hash":"98e81d78…","schema_version":1}
```

That hash makes a recorded session or a benchmark result self-describing — you can tell months
later exactly which configuration produced it. It also means two processes that disagree about
the configuration say so immediately, rather than producing quietly wrong output. Reproduce it
with `shasum -a 256 config/quant_arena.toml`.

### Configuration

`config/quant_arena.toml` is the single version-controlled configuration file, read by every
process. Domain parameters live there and nowhere else — there are no environment overrides and
no code defaults, so a missing value is a startup error rather than a silent fallback.

The environment carries only *infrastructure*: `QA_REDIS_URL`, `QA_DATABASE_URL`,
`QA_SESSION_COOKIE_SECURE`. These differ between a laptop, CI and Docker while the configuration
is identical, which is why they sit outside the hash.

### Developing without Docker

Run the stores in containers and the gateway on the host, so reloads are instant:

```bash
docker compose up -d redis postgres
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m uvicorn services.gateway.app:app --reload --port 8000
```

### Tests

```bash
docker compose up -d redis postgres
.venv/bin/python -m pytest -q
```

The suite runs against the real Redis and PostgreSQL, not fakes. `contracts/v1/tests` also
compiles the generated C++ header with a C++20 compiler; without one it skips loudly, and a
green run carrying that skip has not verified C++/Python size parity.

------------------------------------------------------------------------

## Project Overview

Quant Arena is a simulated electronic trading and quantitative research
platform.

Users will be able to trade using virtual money, view a live market,
test trading ideas, analyze their performance, and eventually
participate in competitions.

The project is **not** a real brokerage and will **not** handle real
money.

Our goal is to build a real product while also learning and
demonstrating how professional trading and high-performance software
systems are designed.

------------------------------------------------------------------------

# 1. Proof of Concept (POC)

The first Proof of Concept (POC) will prove that the core idea of Quant
Arena works.

The POC will focus on a small but complete trading flow instead of
trying to build every feature at once.

## What the POC should allow

A user should be able to:

1.  Create an account.
2.  Receive virtual money.
3.  View a small set of simulated assets.
4.  View live prices and a basic order book.
5.  Place a buy or sell order.
6.  Cancel an open order.
7.  Have the order checked and processed.
8.  Match compatible buy and sell orders.
9.  See completed trades in real time.
10. See their portfolio update after a trade.

The first version will support:

-   **Limit orders**
-   **Market orders**, if feasible after the basic matching flow is
    stable
-   Order cancellation
-   Partial and full order execution
-   Basic price-time priority
-   Live market updates
-   Basic portfolio tracking

## POC User Flow

``` mermaid
flowchart TD
    A[User opens Quant Arena] --> B[Views live market]
    B --> C[Places buy or sell order]
    C --> D[Order is validated]
    D --> E[Basic risk checks]
    E --> F[Matching engine]

    F --> G{Matching order available?}

    G -->|Yes| H[Trade is executed]
    G -->|No| I[Order is added to order book]

    H --> J[Portfolio is updated]
    H --> K[Market data is updated]

    J --> L[User sees result in real time]
    K --> L

    I --> M[User sees open order]
```

## POC Boundary

The POC is intentionally limited.

### Included

-   User authentication
-   Virtual account balance
-   Simulated assets
-   Order submission
-   Order cancellation
-   Basic order book
-   Matching engine
-   Trade execution
-   Portfolio updates
-   Real-time market updates

### Not required in the first POC

-   Real money
-   Real stock exchange integration
-   Complex financial regulations
-   User-written strategies
-   Advanced backtesting
-   AI-generated strategies
-   Kubernetes
-   Large-scale microservices
-   High availability across multiple regions

These features can be added later after the core system is working
correctly.

------------------------------------------------------------------------

# 2. What Problems Will Quant Arena Handle?

Quant Arena is designed to solve several problems that naturally appear
in a real-time trading platform.

## Problem 1: Matching buyers and sellers

A buyer may want to buy an asset at a certain price while another user
wants to sell it.

The system needs to decide:

-   Whether the two orders can be matched.
-   How much of each order can be executed.
-   Which order gets priority.

``` mermaid
flowchart LR
    A[Buy Order] --> C[Matching Engine]
    B[Sell Order] --> C
    C --> D{Can they match?}
    D -->|Yes| E[Execute Trade]
    D -->|No| F[Keep Order in Order Book]
```

------------------------------------------------------------------------

## Problem 2: Maintaining a correct order book

Many users may place, cancel, or modify orders at nearly the same time.

The system needs to maintain a correct view of:

-   Buy orders
-   Sell orders
-   Prices
-   Quantities
-   Order priority

A major challenge is ensuring that the order book stays correct even
when many events happen quickly.

------------------------------------------------------------------------

## Problem 3: Processing events in the correct order

In a trading system, the order in which events are processed can change
the result.

For example:

``` text
Order A arrives first.
Order B arrives second.
```

The system must process them consistently according to the rules of the
exchange.

This is important for:

-   Fairness
-   Correctness
-   Reproducibility

------------------------------------------------------------------------

## Problem 4: Preventing duplicate processing

A network problem may cause a client to send the same request more than
once.

Without protection, the system could accidentally create multiple
identical orders.

Quant Arena must be able to identify and safely handle duplicate
requests.

------------------------------------------------------------------------

## Problem 5: Updating users in real time

When a trade happens, several things may need to change:

-   Market price
-   Order book
-   User portfolio
-   User balance
-   Trade history

Users should receive important updates without manually refreshing the
page.

``` mermaid
flowchart TD
    A[Trade Executed] --> B[Update Portfolio]
    A --> C[Update Order Book]
    A --> D[Publish Market Event]

    D --> E[Real-time Service]

    B --> F[User Dashboard]
    C --> F
    E --> F
```

------------------------------------------------------------------------

## Problem 6: Separating important work from non-critical work

The matching engine is a critical part of the system.

Activities such as:

-   Analytics
-   Logging
-   Notifications
-   Historical reporting

should not unnecessarily slow down the trade processing path.

The project will explore asynchronous event processing to separate these
responsibilities.

------------------------------------------------------------------------

## Problem 7: Quantitative strategy testing

A user may have an idea for a trading strategy.

For example:

> "Buy when the short-term price trend rises above the long-term trend."

The platform should eventually allow users to test such ideas against
market data.

The system needs to:

-   Load historical or simulated data.
-   Run the strategy.
-   Simulate trades.
-   Track portfolio changes.
-   Calculate performance metrics.

``` mermaid
flowchart LR
    A[Market Data] --> B[Trading Strategy]
    B --> C[Backtesting Engine]
    C --> D[Simulated Trades]
    D --> E[Portfolio Results]
    E --> F[Performance Metrics]
```

------------------------------------------------------------------------

## Problem 8: Measuring risk

A trading system should not only look at profit.

It should also help measure risk.

For example:

-   How much money was lost during the worst period?
-   How volatile were the returns?
-   How much of the portfolio was exposed to one asset?
-   Did the user exceed a defined trading limit?

------------------------------------------------------------------------

## Problem 9: Recovering from failures

Software systems can fail.

A service may crash while processing events.

Quant Arena should eventually be able to answer:

-   What was the last known state?
-   Which events were already processed?
-   How can the system recover safely?

The long-term goal is to support event logging and replay.

``` mermaid
flowchart TD
    A[Order and Trade Events] --> B[Durable Event Log]
    B --> C[System Failure]
    C --> D[Service Restarts]
    D --> E[Replay Required Events]
    E --> F[Recover System State]
```

------------------------------------------------------------------------

# 3. Primary Objective

The primary objective of Quant Arena is:

> **To build a real-time simulated electronic trading platform with a
> correct and reliable exchange core, while allowing users to research,
> test, and eventually compete using quantitative trading strategies.**

The project should demonstrate the complete journey from:

``` mermaid
flowchart LR
    A[User Action] --> B[Order Processing]
    B --> C[Trade Execution]
    C --> D[Real-time Market Updates]
    D --> E[Portfolio Tracking]
    E --> F[Data Analysis]
    F --> G[Strategy Research and Backtesting]
```

The primary focus is to first build a **correct working system**.

Performance and scale are important, but we will not sacrifice
correctness just to claim that the system is fast.

------------------------------------------------------------------------

# 4. Goals That Need to Be Achieved

The project goals are divided into six major areas.

## Goal 1: Build a correct exchange core

The system must correctly handle:

-   Buy orders
-   Sell orders
-   Limit orders
-   Order cancellation
-   Partial execution
-   Full execution
-   Order book updates
-   Price-time priority

The same sequence of input events should produce the same result.

### Success for this goal

We should have strong automated tests that prove the matching engine
behaves correctly in different scenarios.

------------------------------------------------------------------------

## Goal 2: Create a real-time user experience

Users should be able to see important changes immediately.

This includes:

-   Price changes
-   Order book updates
-   Trade execution
-   Order status
-   Portfolio changes

### Success for this goal

A user should be able to place an order and observe its result in the
application without refreshing the page.

------------------------------------------------------------------------

## Goal 3: Build a quantitative research and backtesting system

The platform should eventually allow users to:

-   Create or select a strategy.
-   Choose market data.
-   Run a backtest.
-   Analyze the results.

The results should include useful metrics such as:

-   Profit and loss
-   Return
-   Win rate
-   Maximum drawdown
-   Volatility
-   Number of trades

### Success for this goal

The same strategy, data, and configuration should produce the same
result when run again.

------------------------------------------------------------------------

## Goal 4: Build a safe strategy execution environment

In later versions, users may be allowed to submit strategies.

User code must not be allowed to damage or control the main system.

The system should eventually provide:

-   Execution time limits
-   CPU limits
-   Memory limits
-   Restricted permissions
-   Isolation from the main application

``` mermaid
flowchart TD
    A[User Strategy] --> B[Validation]
    B --> C[Isolated Execution Environment]
    C --> D{Execution Safe and Valid?}
    D -->|Yes| E[Generate Simulated Trading Actions]
    D -->|No| F[Stop Execution and Return Error]
```

### Success for this goal

A badly written or malicious strategy should not crash the main platform
or affect other users.

------------------------------------------------------------------------

## Goal 5: Measure and improve performance

We will measure performance instead of making unsupported claims.

The team should benchmark:

-   Orders processed per second
-   Average latency
-   p50 latency
-   p95 latency
-   p99 latency
-   Number of connected users
-   Real-time update delay
-   Recovery time after failures

### Success for this goal

We should be able to publish benchmark results and explain:

-   How the tests were performed.
-   What bottlenecks were found.
-   What improvements were made.
-   How the improvements affected the results.

------------------------------------------------------------------------

## Goal 6: Evolve toward a production-like system

As the project grows, we should add professional engineering practices.

This includes:

-   Containerization
-   Automated testing
-   CI/CD
-   Cloud deployment
-   Monitoring
-   Metrics
-   Structured logging
-   Distributed tracing
-   Load testing
-   Failure testing

``` mermaid
flowchart LR
    A[Code Change] --> B[Automated Tests]
    B --> C[Build Application]
    C --> D[Create Container]
    D --> E[Deploy]
    E --> F[Monitor System]
    F --> G[Collect Metrics and Logs]
```

### Success for this goal

The team should be able to deploy the system, monitor its health,
identify failures, and understand how a request or event moved through
the system.

------------------------------------------------------------------------

# 5. Secondary Objective

The secondary objective is:

> **To use Quant Arena as a learning and demonstration project for
> professional software engineering, system design, performance
> engineering, cloud infrastructure, security, and quantitative
> systems.**

We do not want to add technologies just to make the architecture diagram
look impressive.

Every major technology or component should answer a real problem.

For example:

  -----------------------------------------------------------------------
  Problem                             Possible Solution
  ----------------------------------- -----------------------------------
  Users need live updates             WebSockets or another real-time
                                      communication method

  Duplicate requests may occur        Idempotency and request tracking

  Analytics should not slow trading   Asynchronous event processing

  Frequently used data needs faster   Caching
  access                              

  A service crashes                   Durable event storage and recovery

  System behavior is difficult to     Logging, metrics, and tracing
  understand                          

  User strategies are unsafe          Isolated execution environment
  -----------------------------------------------------------------------

The system should evolve step by step.

``` mermaid
flowchart LR
    A[Working Core] --> B[Real-time Features]
    B --> C[Background and Event Processing]
    C --> D[Quant Research]
    D --> E[Performance Optimization]
    E --> F[Cloud and Production Engineering]
    F --> G[Scale and Reliability Improvements]
```

This approach allows the team to understand **why** each architectural
decision was made.

------------------------------------------------------------------------

# 6. Definition of Success

Quant Arena will be considered successful when it satisfies both the
**product goal** and the **engineering goal**.

## Product Success

A real user should be able to:

1.  Create an account.
2.  Receive virtual capital.
3.  View a live simulated market.
4.  Submit buy and sell orders.
5.  See orders processed correctly.
6.  View a live order book.
7.  See completed trades.
8.  See portfolio and balance changes.
9.  Eventually test a trading strategy using market data.
10. Understand their trading and strategy performance.

The complete basic flow should work as follows:

``` mermaid
flowchart TD
    A[User] --> B[View Market]
    B --> C[Place Order]
    C --> D[Validate and Check Risk]
    D --> E[Matching Engine]
    E --> F[Trade or Open Order]
    F --> G[Update Portfolio]
    F --> H[Update Market Data]
    G --> I[Real-time User Dashboard]
    H --> I
```

------------------------------------------------------------------------

## Engineering Success

The team should be able to demonstrate:

### Correctness

-   Matching rules work correctly.
-   Partial fills work correctly.
-   Order cancellation works correctly.
-   Duplicate requests are handled safely.
-   Important system state remains consistent.

### Real-time behavior

-   Users receive live market updates.
-   Trade results are visible quickly.
-   Portfolio updates are propagated correctly.

### Quantitative capabilities

-   Strategies can be tested against data.
-   Results are reproducible.
-   Useful performance and risk metrics are generated.

### Performance understanding

The team should have benchmark reports showing:

-   Throughput
-   Latency
-   Bottlenecks
-   Improvements

### Reliability

The team should understand and demonstrate:

-   What happens when a component fails.
-   How important state is recovered.
-   How failures are detected.

### Production engineering

The system should eventually include:

-   Automated tests
-   Automated build and deployment
-   Containerized services
-   Cloud deployment
-   Monitoring
-   Logging
-   Tracing

------------------------------------------------------------------------

# Final Success Statement

Quant Arena should not end as simply:

> "A website where users buy and sell fake stocks."

It should become:

> **A real-time simulated trading platform that demonstrates how an
> electronic exchange, market data system, quantitative research
> environment, and production software infrastructure can work
> together.**

At the end of the project, the team should be able to explain:

-   What problem each major component solves.
-   Why the architecture evolved in a particular way.
-   How correctness is maintained.
-   How the system handles real-time events.
-   How performance was measured and improved.
-   How failures are detected and handled.
-   How quantitative strategies are tested safely.

------------------------------------------------------------------------

# Appendix: Project Vocabulary

This table defines common Quant Arena terms in simple language.

| Term | Simple Definition |
|---|---|
| **Asset** | Something that can be bought or sold in the simulated market. Example: a simulated stock called `QA-TECH`. |
| **Exchange** | The system where buyers and sellers submit orders and trades are matched. In Quant Arena, the exchange is simulated. |
| **Order** | A request to buy or sell an asset. |
| **Buy Order** | An order from a user who wants to purchase an asset. |
| **Sell Order** | An order from a user who wants to sell an asset. |
| **Limit Order** | An order where the user specifies the maximum price they will pay when buying or the minimum price they will accept when selling. |
| **Market Order** | An order that tries to execute immediately using the best available prices. |
| **Matching Engine** | The core component that decides whether buy and sell orders can trade with each other. |
| **Order Book** | A structured list of active buy and sell orders that have not yet been completely executed. |
| **Trade** | A completed transaction between a buyer and a seller. |
| **Partial Fill** | When only part of an order is executed. For example, a request to buy 100 units may initially execute only 40 units. |
| **Price-Time Priority** | A rule for deciding which order gets priority: generally, a better price gets priority, and if prices are equal, the earlier order gets priority. |
| **Portfolio** | The collection of assets, cash, and positions owned by a user. |
| **Position** | The amount of a particular asset currently held by a user. |
| **Market Data** | Information about the current or past state of the market, such as prices, trades, order book updates, and trading volume. |
| **Strategy** | A defined set of rules used to decide when to buy or sell. |
| **Quantitative Strategy** | A strategy based on data, mathematical rules, statistics, or algorithms rather than manual decisions alone. |
| **Backtesting** | Testing a trading strategy using historical or simulated past market data to understand how it would have performed. |
| **Paper Trading** | Trading in a simulated environment using virtual money instead of real money. |
| **Risk** | The possibility of losing money or taking an unwanted level of exposure. |
| **Risk Check** | A check performed before or during trading to ensure an action does not break defined limits. |
| **Maximum Drawdown** | The largest drop in portfolio value from a previous high point to a later low point. |
| **Volatility** | A measure of how much values move up and down over time. Higher volatility generally means larger or more frequent changes. |
| **Latency** | The time taken for something to happen after a request or event occurs. Example: the time between submitting an order and receiving the result. |
| **Throughput** | The amount of work a system can process in a given amount of time, such as orders processed per second. |
| **p50, p95, and p99 Latency** | Measurements that show latency across requests. p50 represents typical latency, while p95 and p99 help show slower requests. |
| **Event** | A record that something happened, such as an order being submitted, cancelled, or executed. |
| **Event Stream** | A sequence of events that can be processed by different parts of the system. |
| **Event Replay** | Reprocessing stored events to rebuild or verify system state. |
| **Idempotency** | The ability to safely process the same request multiple times without accidentally producing multiple results. |
| **Real-Time Update** | Information sent to a user as soon as something important changes, without requiring a page refresh. |
| **WebSocket** | A technology that allows the server and client to maintain an open connection for real-time communication. |
| **Backtesting Engine** | The component that runs a strategy against market data and calculates the results. |
| **Matching** | The process of finding compatible buy and sell orders and executing a trade between them. |
| **Deterministic** | A system is deterministic when the same input, processed under the same rules, produces the same result. |
| **Benchmark** | A controlled test used to measure system performance, such as orders processed per second or p99 latency. |
| **Observability** | The ability to understand what is happening inside a running system using logs, metrics, and traces. |
| **CI/CD** | Automation that helps test, build, and deploy software changes. |
| **Sandbox / Isolated Execution Environment** | A restricted environment used to safely run potentially unsafe code, such as user-created strategies. |

