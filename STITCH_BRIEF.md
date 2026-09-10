# Quant Arena — Product Requirements Brief

A brief for generating screen designs. It states **what the product does, what each screen must
contain, and how each element behaves**. It deliberately makes no statement about colour,
typography, imagery, or any other visual treatment — those are entirely open.

---

## 1. What the product is

Quant Arena is a simulated stock exchange for a web browser. Users sign in, receive a starting
balance of virtual money, and trade ten simulated instruments against a live, continuously
updating market. They can also test an automated trading strategy against recorded historical
market data and read the results.

No real money and no real exchange is involved at any point.

## 2. Who uses it

A single class of user: a signed-in trader. There are no administrators, no moderators and no
account tiers. Every signed-in user sees the same capabilities.

## 3. Scope — exactly three screens

The product has **three screens and no others**. Do not introduce a settings screen, a profile
screen, an admin screen, a dashboard, an onboarding flow, a notification centre, or a landing
page.

1. **Trading** — the main screen, at the root path
2. **Sign in / Register** — at `/login`
3. **Backtest** — at `/backtest`

## 4. Application shell — present on all three screens

- A navigation control offering the three screens above, indicating which one is current.
- A **connection indicator**, visible at all times, never a transient message. It shows exactly
  one of five states, each distinguishable from the others:
  - **Connecting** — the live data connection is being established
  - **Live** — connected and receiving current market data
  - **Reconnecting** — the connection dropped and is being re-established
  - **Exchange halted** — the connection is healthy and prices are current, but the exchange
    cannot accept orders. This is *not* a connection failure and must not be presented as one.
  - **Disconnected** — no live connection
- An indication of who is signed in, and a way to sign out.

---

## 5. Screen 1 — Trading

The primary screen. It shows one instrument at a time and updates continuously — the order book
refreshes roughly twenty times per second, and trades appear as they happen.

### 5.1 Instrument selector

- One selectable entry per listed instrument (ten of them).
- Each entry shows the instrument's name **and its own current price**, and every entry's price
  updates live, including the nine that are not currently selected.
- Exactly one entry is selected at a time; selecting one changes what every panel below shows.
- A price that has not yet traded shows a placeholder rather than a zero or a blank.

### 5.2 Price chart

- A candlestick chart of the selected instrument, one candle per second of elapsed time, with a
  time axis and a price axis.
- New candles append continuously while the screen is open.
- Changing the selected instrument replaces the chart contents.

### 5.3 Order book

- Two sides — buy orders and sell orders — **ten price levels each**, always ten, whether or not
  every level currently has orders in it.
- Each level shows a price, a quantity, and a proportional indicator of that quantity relative
  to the largest quantity currently on screen.
- The best buy price and the best sell price sit adjacent to one another, separated by a
  **spread** readout showing the difference between them.
- A level with no orders is shown blank. Rows are never added or removed and the panel never
  changes size, so a price the user is about to click never moves under the pointer.
- Clicking a price level fills that price into the order form.

### 5.4 Trade tape

- A running list of the most recent trades in the selected instrument, newest first, each row
  showing price, quantity and time.
- A fixed number of rows; older trades fall off the end.
- Each row indicates whether the trade happened at the buy side or the sell side of the book.

### 5.5 Order ticket

A form for submitting an order. Fields and controls:

- **Side** — buy or sell, a two-way choice with one always selected.
- **Order type** — limit or market.
- **Quantity** — a whole number, required, must be positive.
- **Price** — required for a limit order; disabled and not required for a market order.
- A control that fills the price field from the current best price on the chosen side, disabled
  when the order type is market.
- A submit control, disabled while a submission is in flight and showing that it is in flight.

Behaviour the design must accommodate:

- **Submitting an order returns an acknowledgement, not an outcome.** The form must never claim
  the order traded, or is resting, at the moment the button is pressed. Its immediate answer is
  that the order was *sent*.
- Outcomes — accepted, rejected, partially filled, filled, cancelled — arrive afterwards and
  independently, and appear as a short list of **recent order events** below the form.
- Validation failures (missing quantity, missing price on a limit order, insufficient balance, a
  price outside the permitted band) are shown as messages attached to the form.

### 5.6 Open orders

- A table of the user's orders currently resting on the exchange.
- Columns: instrument, side, price, quantity remaining, and a cancel control per row.
- A cancelled order disappears **when the exchange confirms it**, not when the button is pressed;
  the row must tolerate a brief period after the click where it is still present.
- An empty state for when there are no open orders.

### 5.7 Portfolio

- The user's cash balance. Before the starting balance has arrived it shows a pending state
  rather than zero.
- A table of positions held: instrument and quantity. Quantities can be negative.
- An empty state for when there are no positions.

### 5.8 Signed-out state of this screen

Market data is only available to a signed-in user. When signed out, this screen must explain that
the user needs to sign in to see the market at all — the chart, book and tape will be empty and
the connection indicator will read Reconnecting — and must offer a route to sign in. It must not
look broken.

---

## 6. Screen 2 — Sign in / Register

One screen serving two modes, carrying only the fields listed below.

- A mode switch between **Sign in** and **Create account**, each with its own heading and a
  one-line explanation. The create-account explanation notes that registering grants starting
  virtual capital.
- Fields, both modes: **username** (3–64 characters) and **password** (8–256 characters, masked).
- A submit control that shows when a request is in flight and is disabled while it is.
- An error area for failures (wrong credentials, username already taken, server errors).
- A success area for confirmations (account created, signed out).
- After registering, the user is signed in automatically — allow for a brief confirmation message
  before the screen changes.
- **Signed-in state of this screen:** the username, the account identifier, a note that the
  session survives a page reload, and a sign-out control.

---

## 7. Screen 3 — Backtest

Runs a chosen automated strategy over recorded historical data and reports how it performed.

### 7.1 Form

- **Strategy** — a picker over the available strategies. Selecting one shows a short description
  of what that strategy does.
- **Instrument** — a picker over the same ten instruments.
- **From day** and **To day** — whole numbers within the range the dataset covers. These are
  **simulated day numbers, not calendar dates**; the dataset carries no wall-clock time, so a
  date picker would show something invented. Do not design one.
- **Bar width** — a choice of 1, 5, 15 or 60 simulated minutes.
- A run control, disabled until an instrument is chosen and while a run is in progress, and
  showing that a run is in progress.
- An error area for a rejected or failed run.

### 7.2 Results

Shown after a successful run, containing, in this order:

1. **A statement of the simulation's limitation**, placed *above* the numbers. Fills are
   simulated at the opening price of each bar rather than against a real order book, and the
   reader must encounter that before the results, not after.
2. **A results table**, one row per metric:
   - Run identifier and number of trades
   - Starting cash
   - Final equity
   - Profit and loss
   - Return, as a percentage
   - Buy-and-hold return over the same period, as a percentage
   - Excess return over buy-and-hold — the headline comparison, and the row that should read as
     the most important
   - Number of trades
   - Win rate
   - Fees paid
   - Maximum drawdown
   - Volatility per bar
   - Sharpe ratio per bar
   - Refused intents, shown only when there were any
3. Where a metric can be negative (profit and loss, return, excess return), the sign must be
   unmistakable at a glance.

There is **no equity curve and no drawdown chart** on this screen. Do not design one.

### 7.3 Signed-out state

A prompt to sign in, and nothing else.

---

## 8. Cross-cutting requirements

- **Numbers are the content.** Prices, quantities and balances are the substance of every screen;
  they must be readable at a glance and must not shift position as they update.
- **Prices must never be silently stale.** Whenever live data is not flowing, the interface says
  so, and the connection indicator is how it says so.
- **Live regions do not reflow.** The book, tape and instrument selector change many times per
  second. Their rows and sizes are fixed so nothing moves under the user's pointer.
- **Every list has an explicit empty state** — no positions, no open orders, no trades yet, no
  results yet, no instruments listed.
- **Every long-running action has an in-progress state** — submitting an order, cancelling an
  order, signing in, running a backtest.
- **Desktop-first.** The trading screen assumes a wide window. It must not break on a narrow one,
  but a phone-optimised layout is not required.
- **Accessibility:** every form control labelled; tables with header rows; the connection
  indicator and the order-event list announced to assistive technology as they change.

## 9. Explicitly out of scope

Settings screens · admin panels · user profiles · onboarding or tutorial flows · notification
centres · marketing or landing pages · in-app chat or social features · leaderboards ·
light/dark mode switching · mobile-specific layouts · deposits, withdrawals or anything
resembling real payment.
