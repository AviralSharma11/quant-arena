"""Task 6.1b — the trading screen's private half: ticket, open orders, portfolio.

Same approach as 6.1a's tests and for the same reason: there is no JavaScript test framework, by
the decision of 2026-08-31. Node strips TypeScript natively, so the modules are imported and
driven directly.

**What these tests reach, and what they do not.** 6.1 carries five success criteria and 6.1b is
where they are claimed. Three of them are mechanism and are proven here: cash and positions move
on a fill without a REST call, cancellation works, and a cancel that loses the race to a fill
leaves the UI correct. Two are about how the screen *feels* under load — "no visible frame drops",
"a user completes the full workflow" — and those need a human at a browser. That deviation is the
same one 5.4c's criterion 5 and 6.1a both carry, and it is recorded rather than papered over.

The fee arithmetic is the interesting case. The private stream carries no balance, because
`schema.toml` has no record that does — the engine is money-blind. So the client applies the
ledger's own maker/taker fees to show cash moving on a fill, which is a second copy of server
logic, and `test_the_client_fee_constants_match_the_ledger` is what stops it drifting.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB = REPO_ROOT / "web"
SRC = WEB / "src"
STREAM = SRC / "stream"
COMPONENTS = SRC / "components"


def code_of(path: Path) -> str:
    """The file with its comments stripped.

    Borrowed from `test_trading_screen.py`, and needed for the same reason: these assertions are
    about what the code *does*, and these modules explain in prose why polling the
    re-synchronisation endpoints would be wrong — which trips a naive substring check on the
    very comment recording the rule.
    """
    out, in_block = [], False
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if in_block:
            if "*/" in stripped:
                in_block = False
            continue
        if stripped.startswith("/*"):
            in_block = "*/" not in stripped
            continue
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        out.append(line)
    return "\n".join(out)


def _node() -> str:
    path = shutil.which("node")
    if path is None:
        pytest.skip("node is not installed — the private half was NOT verified")
    return path


def run_script(body: str) -> dict:
    node = _node()
    script = STREAM / "__probe_6_1b.mts"
    script.write_text(body)
    try:
        result = subprocess.run(
            [node, "--experimental-strip-types", str(script)],
            capture_output=True, text=True, cwd=WEB, timeout=60,
        )
    finally:
        script.unlink(missing_ok=True)
    if result.returncode != 0:
        pytest.fail(f"node exited {result.returncode}:\n{result.stdout}\n{result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


# --- the reducer: cash and positions move on the stream, not on a poll --------------------------


def test_the_grant_arrives_on_the_private_stream():
    """Registration is acknowledgement-shaped and its response carries no `cash_ticks`
    (`routes_auth.py`) — the grant is an event the matcher forwards. So `AccountCreated` on the
    private stream is the first moment a client can know its balance, and until then the panel
    must say "unknown" rather than "zero"."""
    result = run_script("""
import { EMPTY, reduce } from "./portfolio.ts";
const before = EMPTY.cashTicks;
const after = reduce(EMPTY, {
  type: "private",
  message: { ch: "private", type: "AccountCreated", seq: "1", ts_ns: 1,
             initial_cash_ticks: 10_000_000_000 },
});
console.log(JSON.stringify({ before, after: after.cashTicks }));
""")
    assert result["before"] is None, "an unknown balance must not be reported as zero"
    assert result["after"] == 10_000_000_000


def test_a_fill_moves_cash_and_position_without_a_rest_call():
    """6.1's first success criterion, in its mechanical form: the balance and the position change
    on the `Fill` itself. A taker buy pays the notional plus the taker fee."""
    result = run_script("""
import { EMPTY, reduce } from "./portfolio.ts";
let state = reduce(EMPTY, {
  type: "private",
  message: { ch: "private", type: "AccountCreated", seq: "1", ts_ns: 1,
             initial_cash_ticks: 10_000_000_000 },
});
state = reduce(state, {
  type: "private",
  message: { ch: "private", type: "Fill", seq: "2", ts_ns: 2, order_id: 5,
             symbol: "QAA", price_ticks: 1_000_000, qty: 3,
             aggressor_side: 1, role: "taker" },
});
console.log(JSON.stringify({
  cash: state.cashTicks, position: state.positions["QAA"],
}));
""")
    notional = 1_000_000 * 3
    taker_fee = (notional * 10) // 10_000
    assert result["cash"] == 10_000_000_000 - (notional + taker_fee)
    assert result["position"] == 3


def test_a_maker_sell_receives_the_notional_less_the_maker_fee():
    """The other three corners of the same arithmetic. `role` says which fee this user paid and
    `aggressor_side` says who crossed, so together they give the direction — which is exactly
    why §3.4 puts `role` on the wire."""
    result = run_script("""
import { EMPTY, reduce } from "./portfolio.ts";
// aggressor_side = BUY (1) and role = maker means this user was the resting SELLER.
const state = reduce({ ...EMPTY, cashTicks: 0 }, {
  type: "private",
  message: { ch: "private", type: "Fill", seq: "1", ts_ns: 1, order_id: 9,
             symbol: "QAB", price_ticks: 500_000, qty: 2,
             aggressor_side: 1, role: "maker" },
});
console.log(JSON.stringify({ cash: state.cashTicks, position: state.positions["QAB"] }));
""")
    notional = 500_000 * 2
    maker_fee = (notional * 2) // 10_000
    assert result["cash"] == notional - maker_fee
    assert result["position"] == -2


def test_the_client_fee_constants_match_the_ledger():
    """The one deliberate duplication of server logic, guarded rather than trusted.

    `stream/portfolio.ts` applies maker/taker fees so cash can move on a fill without a REST
    round trip — the private stream carries no balance, because the engine is money-blind and
    `schema.toml` has no record that could. That makes two copies of one rule, so this test
    reads both and fails the moment they disagree.
    """
    ledger = (REPO_ROOT / "services" / "ledger" / "ledger.py").read_text()
    client = (STREAM / "portfolio.ts").read_text()

    def one(pattern: str, source: str) -> int:
        found = re.search(pattern, source)
        assert found is not None, f"could not find {pattern}"
        return int(found.group(1).replace("_", ""))

    assert one(r"MAKER_FEE_BPS[^=]*=\s*(\d+)", ledger) == one(
        r"MAKER_FEE_BPS\s*=\s*(\d[\d_]*)", client
    )
    assert one(r"TAKER_FEE_BPS[^=]*=\s*(\d+)", ledger) == one(
        r"TAKER_FEE_BPS\s*=\s*(\d[\d_]*)", client
    )
    assert one(r"BPS_DENOMINATOR[^=]*=\s*(\d[\d_]*)", ledger) == one(
        r"BPS_DENOMINATOR\s*=\s*(\d[\d_]*)", client
    )


# --- open orders, and the race the criterion names ----------------------------------------------


def test_an_order_appears_shrinks_and_leaves_on_the_stream_alone():
    """The whole life of a resting order, driven only by private messages. Nothing here polls
    `GET /orders/open`; 6.1's Boundaries forbid it and that endpoint is for gap repair only."""
    result = run_script("""
import { EMPTY, openOrderList, reduce } from "./portfolio.ts";
const send = (state, message) => reduce(state, { type: "private", message });
let s = send(EMPTY, { ch: "private", type: "OrderAccepted", seq: "1", ts_ns: 1,
  order_id: 77, client_order_id: 1042, symbol: "QAA",
  price_ticks: 1000, qty: 10, side: 1, tif: 1 });
const accepted = openOrderList(s).map((o) => [o.orderId, o.remainingQty]);

s = send(s, { ch: "private", type: "Fill", seq: "2", ts_ns: 2, order_id: 77,
  symbol: "QAA", price_ticks: 1000, qty: 4, aggressor_side: 2, role: "maker" });
const partial = openOrderList(s).map((o) => [o.orderId, o.remainingQty]);

s = send(s, { ch: "private", type: "OrderCancelled", seq: "3", ts_ns: 3, order_id: 77,
  client_order_id: 1042, symbol: "QAA", remaining_qty: 6, reason: 1 });
const cancelled = openOrderList(s).map((o) => [o.orderId, o.remainingQty]);

console.log(JSON.stringify({ accepted, partial, cancelled }));
""")
    assert result["accepted"] == [[77, 10]]
    assert result["partial"] == [[77, 6]], "a fill must reduce the resting quantity"
    assert result["cancelled"] == []


def test_a_cancel_that_loses_the_race_to_a_fill_leaves_the_ui_correct():
    """6.1's third success criterion.

    The order fills completely, and only then does the cancel come back — the engine answers
    `UNKNOWN_ORDER`, because there is no longer an order to cancel. The row must already be gone,
    removed by the fill rather than by the cancel, and the rejection must not resurrect it or
    double-count anything.
    """
    result = run_script("""
import { EMPTY, openOrderList, reduce } from "./portfolio.ts";
const send = (state, message) => reduce(state, { type: "private", message });
let s = send({ ...EMPTY, cashTicks: 1_000_000 }, {
  ch: "private", type: "OrderAccepted", seq: "1", ts_ns: 1,
  order_id: 88, client_order_id: 2001, symbol: "QAA",
  price_ticks: 100, qty: 5, side: 2, tif: 1 });

// The fill wins the race and takes the whole order.
s = send(s, { ch: "private", type: "Fill", seq: "2", ts_ns: 2, order_id: 88,
  symbol: "QAA", price_ticks: 100, qty: 5, aggressor_side: 1, role: "maker" });
const afterFill = openOrderList(s).length;
const cashAfterFill = s.cashTicks;

// The cancel arrives second and is refused: UNKNOWN_ORDER (8).
s = send(s, { ch: "private", type: "OrderRejected", seq: "3", ts_ns: 3,
  order_id: null, client_order_id: 2002, symbol: "QAA", reason: 8 });

console.log(JSON.stringify({
  afterFill,
  afterRejection: openOrderList(s).length,
  cashUnchangedByRejection: s.cashTicks === cashAfterFill,
  lastNotice: s.notices[0].kind,
}));
""")
    assert result["afterFill"] == 0, "a fully filled order must leave the book on the Fill"
    assert result["afterRejection"] == 0, "a refused cancel must not resurrect the order"
    assert result["cashUnchangedByRejection"] is True
    assert result["lastNotice"] == "rejected"


# --- re-synchronisation actually repairs state ---------------------------------------------------


def test_a_resync_replaces_state_rather_than_merging_it():
    """A resync happens because a message was *lost* (§3.5), so local state is wrong in an
    unknown way — and merging a correct snapshot into unknown-wrong state preserves exactly the
    error it was fetched to remove.

    This is also the fix for the 6.1a placeholder, which fetched both endpoints and discarded the
    responses: the gap path cost two round trips and repaired nothing.
    """
    result = run_script("""
import { EMPTY, openOrderList, reduce } from "./portfolio.ts";
// A client that believes it holds a position and an order, both wrong.
let s = reduce(EMPTY, { type: "private", message: {
  ch: "private", type: "OrderAccepted", seq: "1", ts_ns: 1, order_id: 1,
  client_order_id: 11, symbol: "QAA", price_ticks: 100, qty: 9, side: 1, tif: 1 } });
s = reduce(s, { type: "private", message: {
  ch: "private", type: "Fill", seq: "2", ts_ns: 2, order_id: 1,
  symbol: "QAA", price_ticks: 100, qty: 4, aggressor_side: 1, role: "taker" } });

s = reduce(s, {
  type: "resync",
  openOrders: [{ order_id: 42, client_order_id: 99, user_id: 1, symbol_id: 2,
                 side: 2, price_ticks: 700, qty: 3, remaining_qty: 3, tif: 1,
                 created_at_ns: 0 }],
  portfolio: { user_id: 1, cash_ticks: 555, positions: [{ symbol_id: 2, qty: -3 }] },
  nameById: { 1: "QAA", 2: "QAB" },
});

console.log(JSON.stringify({
  cash: s.cashTicks,
  positions: s.positions,
  orders: openOrderList(s).map((o) => [o.orderId, o.symbol, o.remainingQty]),
  resyncs: s.resyncs,
}));
""")
    assert result["cash"] == 555, "the server's balance must replace the client's estimate"
    assert result["positions"] == {"QAB": -3}, "the stale QAA position must be gone, not merged"
    assert result["orders"] == [[42, "QAB", 3]]
    assert result["resyncs"] == 1


def test_the_resync_endpoints_are_read_and_applied_not_discarded():
    """The 6.1a placeholder called both endpoints and threw the responses away. `App.tsx` must
    now parse them and dispatch a resync — the calls exist solely to repair state."""
    source = (SRC / "App.tsx").read_text()
    assert 'dispatch({' in source and '"resync"' in source, (
        "App must dispatch a resync action built from the two endpoints' bodies"
    )
    assert "await ordersResponse.json()" in source
    assert "await portfolioResponse.json()" in source


# --- submission and cancellation speak the gateway's actual shapes --------------------------------


def test_a_limit_order_is_submitted_in_integer_ticks_with_a_symbol_id():
    """Two things the frozen contract's §2.2 example gets wrong and the gateway does not: the
    request carries `symbol_id` rather than a name, and the price is integer ticks converted at
    the symbol's own scale. A float below the presentation layer is a bug (Open Issue 016), and
    this function is the boundary where the conversion happens."""
    result = run_script("""
import { submitOrder } from "./orders.ts";
const symbol = { symbol_id: 7, name: "QAG", tick_size_ticks: 100_000, lot_size: 100 };
let captured = null;
const fakeFetch = async (url, init) => {
  captured = { url, body: JSON.parse(init.body), method: init.method };
  return { ok: true, status: 202, json: async () => ({ status: "accepted", seq: "1-0" }) };
};
const ack = await submitOrder(
  { symbol, side: 1, orderType: "limit", qty: 2, displayPrice: 85.28 },
  fakeFetch, () => 1_700_000_000_000,
);
console.log(JSON.stringify({ captured, ack }));
""")
    body = result["captured"]["body"]
    assert result["captured"]["url"] == "/orders"
    assert body["symbol_id"] == 7, "the gateway takes an id, not a name"
    assert "symbol" not in body
    assert body["price_ticks"] == 8_528_000, "85.28 at a 100,000 tick size, as an integer"
    assert isinstance(body["price_ticks"], int)
    assert body["qty"] == 2 and body["side"] == 1 and body["order_type"] == "limit"
    assert result["ack"]["status"] == "accepted"


def test_a_market_order_omits_the_price_entirely():
    """The gateway's model validator refuses a market order that carries `price_ticks` — the
    band derives it from the opposing touch — so sending one is a 400 before anything else."""
    result = run_script("""
import { submitOrder } from "./orders.ts";
const symbol = { symbol_id: 1, name: "QAA", tick_size_ticks: 100, lot_size: 1 };
let captured = null;
const fakeFetch = async (url, init) => {
  captured = JSON.parse(init.body);
  return { ok: true, status: 202, json: async () => ({ status: "accepted", seq: "1-0" }) };
};
await submitOrder({ symbol, side: 2, orderType: "market", qty: 4 }, fakeFetch, () => 1);
console.log(JSON.stringify({ captured, hasPrice: "price_ticks" in captured }));
""")
    assert result["hasPrice"] is False
    assert result["captured"]["order_type"] == "market"


def test_a_cancel_sends_a_body_with_its_own_idempotency_key():
    """Open Issue 008's two identifiers. The path names the order being withdrawn; the body
    carries the cancel's *own* key, so a cancel whose response was lost can be retried without
    risking a second one. §2.2 shows no body, which is the third of the three divergences."""
    result = run_script("""
import { cancelOrder } from "./orders.ts";
let captured = null;
const fakeFetch = async (url, init) => {
  captured = { url, method: init.method, body: JSON.parse(init.body) };
  return { ok: true, status: 202, json: async () => ({ status: "accepted", seq: "9-0" }) };
};
const ack = await cancelOrder(4242, fakeFetch, () => 1_700_000_000_000);
console.log(JSON.stringify({ captured, ackKey: ack.clientOrderId }));
""")
    assert result["captured"]["url"] == "/orders/4242"
    assert result["captured"]["method"] == "DELETE"
    key = result["captured"]["body"]["client_order_id"]
    assert key != 4242, "the cancel needs its own key, not the target's"
    assert key == result["ackKey"]


def test_a_client_order_id_is_never_a_counter_from_one():
    """The trap recorded twice — in the decision log and in `HANDOFF.md` §5 — from Task 4.4's
    bots. A counter restarting at 1 re-sends keys the idempotency store has already answered:
    every order acknowledged, none appended, the market silent while everyone reports success.
    A page reload restarts a counter exactly the way a bot restart does."""
    result = run_script("""
import { nextClientOrderId } from "./orders.ts";
const first = nextClientOrderId(() => 1_700_000_000_000);
const second = nextClientOrderId(() => 1_700_000_000_000);
console.log(JSON.stringify({ first, second, distinct: first !== second }));
""")
    assert result["first"] > 1_000_000_000, "ids must be timestamp-based, not a small counter"
    assert result["distinct"] is True, "two clicks in the same millisecond must not collide"


def test_a_rate_limited_submission_is_not_reported_as_a_rejection():
    """429 means "not processed, ask again" — nothing was recorded against the key, because rate
    limiting runs ahead of the idempotency claim by design. Treating it as a rejection would tell
    a user their order was refused when it was never seen."""
    result = run_script("""
import { submitOrder } from "./orders.ts";
const symbol = { symbol_id: 1, name: "QAA", tick_size_ticks: 100, lot_size: 1 };
const fakeFetch = async () => ({
  ok: false, status: 429,
  json: async () => ({ detail: { status: "rate_limited", reason: "MAX_ORDERS_PER_SECOND" } }),
});
const ack = await submitOrder(
  { symbol, side: 1, orderType: "limit", qty: 1, displayPrice: 1 }, fakeFetch, () => 1);
console.log(JSON.stringify({ status: ack.status, ok: ack.ok }));
""")
    assert result["status"] == "rate_limited"
    assert result["ok"] is False


def test_an_in_progress_duplicate_is_not_shown_as_a_placed_order():
    """A retry that arrived while the original was still in flight comes back 202 with
    `status: "in_progress"`. It is not a second order and must not be reported as one."""
    result = run_script("""
import { submitOrder } from "./orders.ts";
const symbol = { symbol_id: 1, name: "QAA", tick_size_ticks: 100, lot_size: 1 };
const fakeFetch = async () => ({
  ok: true, status: 202, json: async () => ({ status: "in_progress", seq: "" }),
});
const ack = await submitOrder(
  { symbol, side: 1, orderType: "limit", qty: 1, displayPrice: 1 }, fakeFetch, () => 1);
console.log(JSON.stringify({ status: ack.status, seq: ack.seq }));
""")
    assert result["status"] == "in_progress"
    assert result["seq"] is None, 'an empty seq is "no sequence", not the string ""'


# --- the private half stays out of the frame loop -------------------------------------------------


def test_the_private_state_module_cannot_reach_react():
    """The mirror of `buffer.ts`'s rule, applied in the opposite direction. The *reducer* must be
    pure and framework-free so it is testable and replayable; React state is where its output
    lives, which is a different claim and is made in `App.tsx`."""
    source = code_of(STREAM / "portfolio.ts")
    assert "from \"react\"" not in source and "from 'react'" not in source


def test_the_screen_still_owns_exactly_one_frame_loop():
    """The 2026-09-07 decision, unchanged by 6.1b: `takeChanged()` clears the changed set, so a
    second loop would consume changes the first needed. The private panels re-render normally
    and must not start one."""
    trading = code_of(SRC / "screens" / "Trading.tsx")
    # Once to import it, once to call it. A second call site is the defect this guards.
    assert trading.count("startFrameLoop") == 2
    assert trading.count("startFrameLoop({") == 1
    for component in ("OrderTicket.tsx", "PortfolioPanel.tsx"):
        source = code_of(COMPONENTS / component)
        assert "startFrameLoop" not in source, f"{component} must not run its own frame loop"


def test_the_private_panels_do_not_poll_the_resync_endpoints():
    """6.1's Boundaries: `GET /orders/open` and `GET /portfolio` are for re-synchronisation only.
    They may appear in `App.tsx`, which handles the gap, and nowhere else on the screen."""
    # The quoted endpoint literal, not the bare path — `"../stream/portfolio"` is an import and
    # contains the substring, which is the sort of false positive that makes a guard useless.
    for name in ("OrderTicket.tsx", "PortfolioPanel.tsx", "../screens/Trading.tsx"):
        source = code_of((COMPONENTS / name).resolve())
        assert '"/orders/open"' not in source, f"{name} must not read the resync endpoint"
        assert '"/portfolio"' not in source, f"{name} must not read the resync endpoint"
        # And no direct fetching at all: submission goes through `stream/orders.ts`, which is
        # the one place the two *action* endpoints are called.
        assert "fetch(" not in source, f"{name} must not call fetch directly"
