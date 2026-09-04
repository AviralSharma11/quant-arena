"""Task 5.4c — the WebSocket client, gap detection and the render loop.

No JavaScript test framework, by the decision recorded on 2026-08-31: Node 24 strips TypeScript
natively, so these modules are imported and driven directly from a script this file writes and
runs. Adding jsdom or vitest to check them would be a second test framework on a closed stack
list, and the modules under test are deliberately DOM-free precisely so it is not needed.

The one criterion this *cannot* fully reach is Success Criterion 5, "no React re-render occurs on
book updates, verifiable in the React profiler" — a profiler is a human at a browser. What is
proven here instead is the **mechanism**: the buffer cannot notify anybody, the modules do not
import React at all, and a thousand messages between two frames produce exactly one paint. The
profiler check itself is recorded as manual rather than claimed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB = REPO_ROOT / "web"
STREAM = WEB / "src" / "stream"

#: Modules that must stay free of React. The rule is the whole design, so it is asserted rather
#: than trusted: the moment book data reaches framework state, every message becomes a render.
DOM_FREE_MODULES = ("buffer.ts", "gaps.ts", "frameLoop.ts", "client.ts", "types.ts", "mock.ts")


def _node() -> str:
    path = shutil.which("node")
    if path is None:
        pytest.skip("node is not installed — the stream client was NOT verified")
    return path


def run_script(body: str) -> dict:
    """Run a script against the real modules and return whatever it prints as JSON."""
    node = _node()
    script = WEB / "src" / "stream" / "__probe.mts"
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


# --- the rule that the whole design rests on ---------------------------------------------------


@pytest.mark.parametrize("module", DOM_FREE_MODULES)
def test_the_stream_modules_do_not_import_react(module: str):
    """High-frequency data must not live in React state (Open Issue 014 §14a), and the cheapest
    way to be certain is that the modules holding it cannot reach the framework at all."""
    source = (STREAM / module).read_text()
    assert "from \"react" not in source and "from 'react" not in source, module


def test_the_buffer_offers_no_way_to_subscribe_to_it():
    """A component cannot accidentally re-render on a write, because there is no mechanism by
    which it could learn one happened. Rendering is pull-based, by construction."""
    surface = run_script("""
import { MarketBuffer } from "./buffer.ts";
const names = new Set([
  ...Object.getOwnPropertyNames(MarketBuffer.prototype),
  ...Object.getOwnPropertyNames(new MarketBuffer()),
]);
console.log(JSON.stringify({ names: [...names] }));
""")["names"]
    forbidden = {"subscribe", "on", "addEventListener", "addListener", "notify", "emit"}
    assert forbidden.isdisjoint(surface), sorted(forbidden.intersection(surface))


def test_a_thousand_messages_between_frames_produce_one_paint():
    """The conflation itself, measured.

    This is the closest an automated test gets to Success Criterion 5. A state-driven component
    would render once per message; the frame loop paints once per frame, whatever arrived.
    """
    out = run_script("""
import { MarketBuffer } from "./buffer.ts";
import { startFrameLoop } from "./frameLoop.ts";

const buffer = new MarketBuffer();
let paints = 0;
const queue: Array<() => void> = [];
const stop = startFrameLoop({
  buffer,
  paint: () => { paints += 1; },
  requestFrame: (cb) => { queue.push(cb); return queue.length; },
  cancelFrame: () => {},
});

const frame = () => { const next = queue.shift(); if (next) next(); };
frame();                                  // the first frame: nothing has arrived
for (let i = 0; i < 1000; i += 1) {
  buffer.writeBook("QAA", { ch: "book:QAA:l2", seq: `1-${i}`, ts_ns: i, bids: [[100 + i, 5]], asks: [] });
}
frame();                                  // one frame after a thousand messages
const afterBurst = paints;
frame();                                  // and again, with nothing new
stop();
console.log(JSON.stringify({ writes: buffer.writes, afterBurst, finalPaints: paints }));
""")
    assert out["writes"] == 1000
    assert out["afterBurst"] == 1, "the frame after the burst must paint exactly once"
    assert out["finalPaints"] == 1, "a frame with nothing new must not paint at all"


def test_the_buffer_keeps_the_latest_snapshot_not_a_queue():
    """Overwriting is safe because every book message is a complete snapshot, so an
    intermediate one that is never painted was lost harmlessly."""
    out = run_script("""
import { MarketBuffer } from "./buffer.ts";
const buffer = new MarketBuffer();
for (const price of [100, 200, 300]) {
  buffer.writeBook("QAA", { ch: "book:QAA:l2", seq: `1-${price}`, ts_ns: price, bids: [[price, 1]], asks: [] });
}
console.log(JSON.stringify({ bid: buffer.book("QAA").bids[0][0] }));
""")
    assert out["bid"] == 300


def test_the_tape_accumulates_but_is_bounded():
    """Prints accumulate rather than overwrite — a dropped trade is a *wrong* tape, not a stale
    one. Bounded, because an unbounded array in a session-long buffer is a memory leak."""
    out = run_script("""
import { MarketBuffer, TAPE_LIMIT } from "./buffer.ts";
const buffer = new MarketBuffer();
for (let i = 0; i < TAPE_LIMIT + 250; i += 1) {
  buffer.writeTrade("QAA", {
    ch: "tape:QAA", seq: `1-${i}`, ts_ns: i, price_ticks: 1000 + i, qty: 1, aggressor_side: 1,
  });
}
const tape = buffer.tape("QAA");
console.log(JSON.stringify({
  limit: TAPE_LIMIT, length: tape.length,
  newest: tape[tape.length - 1].priceTicks,
  oldest: tape[0].priceTicks,
}));
""")
    assert out["length"] == out["limit"]
    assert out["newest"] == 1000 + out["limit"] + 249, "the newest print must survive"
    assert out["oldest"] == 1000 + 250, "trimmed from the front"


# --- gap detection --------------------------------------------------------------------------


def test_a_jump_on_a_market_data_channel_is_not_a_gap():
    """`seq` is the Redis stream id and a channel carries only some of the stream's records, so
    a jump is entirely normal — the records in between went to other channels. Treating it as
    loss would fire a re-synchronisation on almost every message."""
    out = run_script("""
import { SequenceTracker } from "./gaps.ts";
const tracker = new SequenceTracker();
const verdicts = ["1700-1", "1700-9", "1701-4", "1900-0"].map((s) => tracker.observe("book:QAA:l2", s));
console.log(JSON.stringify({ verdicts, gaps: tracker.privateGaps }));
""")
    assert out["verdicts"] == ["ok", "ok", "ok", "ok"]
    assert out["gaps"] == 0


def test_stream_ids_are_compared_numerically_not_as_strings():
    """`"9-0"` sorts after `"10-0"` lexicographically, which would make every id past the ninth
    millisecond look out of order and fire a fault on a healthy feed."""
    out = run_script("""
import { compareStreamId } from "./gaps.ts";
console.log(JSON.stringify({
  nine_before_ten: compareStreamId("9-0", "10-0") < 0,
  ordinal: compareStreamId("100-2", "100-11") < 0,
  equal: compareStreamId("100-2", "100-2") === 0,
}));
""")
    assert out == {"nine_before_ten": True, "ordinal": True, "equal": True}


def test_a_decreasing_sequence_is_reported_as_out_of_order():
    """Not a gap, but a real fault: messages arriving behind one already seen."""
    out = run_script("""
import { SequenceTracker } from "./gaps.ts";
const tracker = new SequenceTracker();
tracker.observe("book:QAA:l2", "1700-9");
const verdict = tracker.observe("book:QAA:l2", "1700-4");
console.log(JSON.stringify({ verdict, outOfOrder: tracker.outOfOrder }));
""")
    assert out["verdict"] == "out_of_order" and out["outOfOrder"] == 1


def test_a_private_sequence_gap_is_detected():
    """The one channel where a gap means something. The private counter is dense — every message
    this user is party to increments it by one — so n followed by n+2 is genuine loss."""
    out = run_script("""
import { SequenceTracker } from "./gaps.ts";
const tracker = new SequenceTracker();
const verdicts = ["1", "2", "5", "6"].map((s) => tracker.observe("private", s));
console.log(JSON.stringify({ verdicts, gaps: tracker.privateGaps }));
""")
    assert out["verdicts"] == ["ok", "ok", "gap", "ok"]
    assert out["gaps"] == 1


def test_a_repeated_private_sequence_is_a_duplicate_not_a_gap():
    out = run_script("""
import { SequenceTracker } from "./gaps.ts";
const tracker = new SequenceTracker();
tracker.observe("private", "7");
console.log(JSON.stringify({ verdict: tracker.observe("private", "7") }));
""")
    assert out["verdict"] == "duplicate"


def test_nothing_is_compared_across_a_reset():
    """After a reconnect the server's position is unknown. Comparing across it would make the
    first message back look like a gap on every channel at once."""
    out = run_script("""
import { SequenceTracker } from "./gaps.ts";
const tracker = new SequenceTracker();
tracker.observe("private", "5");
tracker.reset();
console.log(JSON.stringify({ verdict: tracker.observe("private", "900"), gaps: tracker.privateGaps }));
""")
    assert out["verdict"] == "ok" and out["gaps"] == 0


# --- the client: subscribe, reconnect, resync -------------------------------------------------

CLIENT_HARNESS = """
import { MarketBuffer } from "./buffer.ts";
import { StreamClient } from "./client.ts";
import { MockStreamSocket } from "./mock.ts";

export function harness(channels = ["book:QAA:l2", "tape:QAA"]) {
  const buffer = new MarketBuffer();
  const timers: Array<{ cb: () => void; ms: number }> = [];
  const states: string[] = [];
  let resyncs = 0;
  let socket: MockStreamSocket | null = null;

  const client = new StreamClient({
    url: "/stream",
    channels,
    buffer,
    onState: (s) => states.push(s),
    onResync: () => { resyncs += 1; },
    random: () => 0.5,                       // no jitter, so delays are assertable
    setTimer: (cb, ms) => { timers.push({ cb, ms }); return timers.length; },
    clearTimer: () => {},
    socketFactory: () => { socket = new MockStreamSocket({ symbols: ["QAA"] }); return socket; },
  });

  return {
    client, buffer, states, timers,
    get socket() { return socket!; },
    get resyncs() { return resyncs; },
    runTimers() { const due = timers.splice(0); for (const t of due) t.cb(); },
  };
}
"""


def run_client_script(body: str) -> dict:
    harness = WEB / "src" / "stream" / "__harness.mts"
    harness.write_text(CLIENT_HARNESS)
    try:
        return run_script(body)
    finally:
        harness.unlink(missing_ok=True)


def test_connecting_subscribes_to_every_channel():
    out = run_client_script("""
import { harness } from "./__harness.mts";
const h = harness(["book:QAA:l2", "tape:QAA", "bars:QAA:1m"]);
h.client.connect();
h.socket.open();
console.log(JSON.stringify({
  sent: h.socket.sent.map((s) => JSON.parse(s)),
  channels: h.socket.subscribedChannels,
  states: h.states,
}));
""")
    assert out["sent"] == [
        {"op": "subscribe", "channels": ["book:QAA:l2", "tape:QAA", "bars:QAA:1m"]}
    ]
    assert out["channels"] == ["book:QAA:l2", "tape:QAA", "bars:QAA:1m"]
    assert out["states"] == ["connecting", "connected"]


def test_a_dropped_socket_reconnects_and_re_subscribes():
    """Success Criterion 2. The subscription is sent on *every* open, not only the first — a
    reconnected socket is a new subscription as far as the server is concerned."""
    out = run_client_script("""
import { harness } from "./__harness.mts";
const h = harness();
h.client.connect();
h.socket.open();
h.socket.drop();
const afterDrop = [...h.states];
h.runTimers();                 // the backoff elapses
h.socket.open();               // the replacement socket connects
console.log(JSON.stringify({
  afterDrop,
  states: h.states,
  connects: h.client.connects,
  subscribes: h.client.subscribes,
  resubscribed: h.socket.subscribedChannels,
}));
""")
    assert out["afterDrop"][-1] == "reconnecting"
    assert out["states"] == ["connecting", "connected", "reconnecting", "connected"]
    assert out["connects"] == 2
    assert out["subscribes"] == 2, "the reconnected socket must re-subscribe"
    assert out["resubscribed"] == ["book:QAA:l2", "tape:QAA"]


def test_backoff_grows_and_then_holds():
    """Jittered in production because two hundred clients dropped by one server restart would
    otherwise all return in lockstep; the jitter source is injected here so the delays are
    assertable."""
    out = run_client_script("""
import { harness } from "./__harness.mts";
const h = harness();
h.client.connect();
h.socket.open();
const delays: number[] = [];
for (let i = 0; i < 7; i += 1) {
  h.socket.drop();
  delays.push(h.timers[h.timers.length - 1].ms);
  h.runTimers();
}
console.log(JSON.stringify({ delays }));
""")
    assert out["delays"] == [250, 500, 1000, 2000, 5000, 5000, 5000]


def test_a_deliberate_private_gap_triggers_exactly_one_resync():
    """Success Criterion 3, and the reason the mock exists: a real server will never
    conveniently skip a sequence number, and this one does it on demand."""
    out = run_client_script("""
import { harness } from "./__harness.mts";
const h = harness();
h.client.connect();
h.socket.open();
h.socket.emitPrivate("OrderAccepted", { order_id: 1 });
h.socket.emitPrivate("Fill", { order_id: 1, qty: 5 });
const before = h.resyncs;
h.socket.skipPrivateSequences(3);
h.socket.emitPrivate("Fill", { order_id: 1, qty: 5 });
h.socket.emitPrivate("OrderCancelled", { order_id: 1 });
console.log(JSON.stringify({
  before, after: h.resyncs, gaps: h.client.tracker.privateGaps, clientResyncs: h.client.resyncs,
}));
""")
    assert out["before"] == 0
    assert out["gaps"] == 1
    assert out["after"] == 1, "one gap, one re-synchronisation"
    assert out["clientResyncs"] == 1


def test_a_book_gap_triggers_no_resync():
    """Self-healing, and ignored on purpose. Every book message is a complete snapshot, so the
    client is already correct again on the next one (§3.5)."""
    out = run_client_script("""
import { harness } from "./__harness.mts";
const h = harness();
h.client.connect();
h.socket.open();
for (let i = 0; i < 40; i += 1) h.socket.emitTick();
console.log(JSON.stringify({
  resyncs: h.resyncs, writes: h.buffer.writes, symbols: h.buffer.symbols(),
}));
""")
    assert out["resyncs"] == 0
    assert out["writes"] > 0
    assert out["symbols"] == ["QAA"]


def test_a_halted_frame_is_its_own_state():
    """The socket is fine and the prices are current; the exchange cannot durably record an
    order (Open Issue 003 §8.5). Conflating that with a network problem tells the user to check
    their wifi."""
    out = run_client_script("""
import { harness } from "./__harness.mts";
const h = harness();
h.client.connect();
h.socket.open();
h.socket.emitError("halted", "redis_unreachable");
console.log(JSON.stringify({ states: h.states, state: h.client.state }));
""")
    assert out["state"] == "halted"
    assert out["states"][-1] == "halted"


def test_a_malformed_frame_does_not_drop_the_connection():
    """One bad frame from the server is a server fault; closing over it would turn it into an
    outage, and the next snapshot repairs the book regardless."""
    out = run_client_script("""
import { harness } from "./__harness.mts";
const h = harness();
h.client.connect();
h.socket.open();
h.socket.onmessage!({ data: "{not json" });
h.socket.emitTick();
console.log(JSON.stringify({ state: h.client.state, writes: h.buffer.writes }));
""")
    assert out["state"] == "connected"
    assert out["writes"] > 0


def test_closing_deliberately_does_not_reconnect():
    out = run_client_script("""
import { harness } from "./__harness.mts";
const h = harness();
h.client.connect();
h.socket.open();
h.client.close();
console.log(JSON.stringify({ state: h.client.state, pendingTimers: h.timers.length }));
""")
    assert out["state"] == "closed"
    assert out["pendingTimers"] == 0, "a deliberate close must schedule no reconnection"


# --- the mock speaks the same wire the server does --------------------------------------------


def test_the_mock_emits_the_contract_shapes():
    """If the mock and `services/fanout/messages.py` drift, the client is being developed
    against a wire that does not exist. Both are pinned to the frozen contract rather than to
    each other, so this asserts the document's field names."""
    out = run_client_script("""
import { harness } from "./__harness.mts";
const h = harness();
const frames: any[] = [];
h.client.connect();
h.socket.open();
const original = h.socket.onmessage!;
h.socket.onmessage = (e) => { frames.push(JSON.parse(e.data)); original(e); };
for (let i = 0; i < 30; i += 1) h.socket.emitTick();
h.socket.emitPrivate("Fill", { order_id: 7, role: "taker" });
const book = frames.find((f) => f.ch === "book:QAA:l2");
const tape = frames.find((f) => f.ch === "tape:QAA");
const priv = frames.find((f) => f.ch === "private");
console.log(JSON.stringify({ book: Object.keys(book), tape: Object.keys(tape), priv: Object.keys(priv) }));
""")
    assert out["book"] == ["ch", "seq", "ts_ns", "bids", "asks"]
    assert out["tape"] == ["ch", "seq", "ts_ns", "price_ticks", "qty", "aggressor_side"]
    assert set(out["priv"]) >= {"ch", "type", "seq", "ts_ns"}


def test_the_mock_carries_no_floats():
    """Money and quantities stay integer ticks even in JSON (§1). The frontend divides by the
    symbol's tick size for display and never sends a divided value back."""
    out = run_client_script("""
import { harness } from "./__harness.mts";
const h = harness();
const numbers: number[] = [];
h.client.connect();
h.socket.open();
const original = h.socket.onmessage!;
h.socket.onmessage = (e) => {
  const walk = (v: any) => {
    if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === "object") Object.values(v).forEach(walk);
    else if (typeof v === "number") numbers.push(v);
  };
  walk(JSON.parse(e.data));
  original(e);
};
for (let i = 0; i < 40; i += 1) h.socket.emitTick();
console.log(JSON.stringify({ total: numbers.length, integers: numbers.every(Number.isInteger) }));
""")
    assert out["total"] > 0
    assert out["integers"] is True
