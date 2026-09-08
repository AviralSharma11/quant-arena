"""Task 6.1a — the trading screen's market half: book, tape and chart.

Same approach as `test_stream_client.py`, and for the same reason: there is no JavaScript test
framework, by the decision of 2026-08-31. Node strips TypeScript natively, so the modules are
imported and driven directly.

**What these tests cannot reach.** Task 6.1's success criteria are about how the screen *looks
and feels* — "book and tape update smoothly under full bot load, with no visible frame drops",
"a user completes the full workflow". Those need a human at a browser and they belong to 6.1b,
which carries all five criteria; 6.1a has none of its own. What is proven here is the mechanism
underneath them: bars behave correctly under reconnection, the symbol table is read from the
contract's only source rather than assumed, prices are formatted at each symbol's own scale, and
nothing on the market-data path can reach React.

The deviation is recorded rather than papered over — the same one 5.4c's criterion 5 carries.
"""

from __future__ import annotations

import json
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

    Needed because these assertions are about what the code *does*, and the modules explain in
    prose why the hard-coded pair was wrong — which would otherwise trip a naive substring
    check on the very comment recording the fix.
    """
    source = path.read_text()
    out, index, in_block = [], 0, False
    for line in source.splitlines():
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
    del index
    return "\n".join(out)


def _node() -> str:
    path = shutil.which("node")
    if path is None:
        pytest.skip("node is not installed — the trading screen was NOT verified")
    return path


def run_script(body: str) -> dict:
    node = _node()
    script = STREAM / "__probe_6_1a.mts"
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


# --- the symbol table comes from the contract's only source ------------------------------------


def test_the_hard_coded_symbol_pair_is_gone():
    """`session.ts` held `STREAM_SYMBOLS = ["QAA", "QAB"]`, which was correct while two
    provisional symbols were all that existed and silently wrong the moment Task 5.1 listed ten
    with four different tick sizes.

    §2.3 makes `GET /symbols` the only source of names and tick sizes, so the constant is gone
    rather than merely lengthened — a longer hard-coded list would have the same defect one
    listing later.
    """
    source = code_of(STREAM / "session.ts")
    assert "STREAM_SYMBOLS" not in source
    assert '"QAA"' not in source and "'QAA'" not in source


def test_no_component_hard_codes_a_symbol_name_or_a_tick_size():
    for path in [*COMPONENTS.glob("*.tsx"), *(SRC / "screens").glob("Trading.tsx")]:
        source = code_of(path)
        assert '"QAA"' not in source, path.name
        assert "tick_size_ticks = 1" not in source, path.name


def test_the_symbol_table_is_fetched_from_the_contracts_endpoint():
    assert '"/symbols"' in (STREAM / "symbols.ts").read_text()


def test_every_symbol_gets_a_book_a_tape_and_a_bar_channel():
    """§3.1. Bars are new in 6.1a — nothing subscribed to them before there was a chart."""
    channels = run_script("""
import { channelsFor } from "./symbols.ts";
const symbols = [
  { symbol_id: 1, name: "QAA", tick_size_ticks: 100, lot_size: 1 },
  { symbol_id: 7, name: "QAG", tick_size_ticks: 100000, lot_size: 100 },
];
console.log(JSON.stringify({ channels: channelsFor(symbols) }));
""")["channels"]
    assert channels == [
        "book:QAA:l2", "tape:QAA", "bars:QAA:1m",
        "book:QAG:l2", "tape:QAG", "bars:QAG:1m",
    ]


# --- prices are formatted at each symbol's own scale -------------------------------------------


def test_prices_are_formatted_at_the_symbols_own_tick_size():
    """Task 5.1 listed four distinct tick sizes, so a client that assumed one scale would
    misprice most of the table. `tick_size_ticks` is the divisor §2.3 defines it as."""
    result = run_script("""
import { formatTicks } from "./symbols.ts";
const qaa = { symbol_id: 1, name: "QAA", tick_size_ticks: 100, lot_size: 1 };
const qag = { symbol_id: 7, name: "QAG", tick_size_ticks: 100000, lot_size: 100 };
console.log(JSON.stringify({
  expensive: formatTicks(7983040, qaa),
  cheap: formatTicks(8979, qag),
}));
""")
    assert result["expensive"] == "79830.40"
    assert result["cheap"] == "0.08979"


def test_formatting_without_a_tick_size_throws_rather_than_guessing():
    """A default of 1 would render a 7,983,040-tick price as "7983040" beside a correctly
    formatted one — a plausible-looking number rather than a visible failure. Open Issue 014
    §14e's rule against quietly showing something wrong covers a misplaced decimal point too.
    """
    assert run_script("""
import { formatTicks } from "./symbols.ts";
let threw = false;
try { formatTicks(100, undefined); } catch { threw = true; }
console.log(JSON.stringify({ threw }));
""")["threw"] is True


def test_display_units_round_trip_back_to_ticks():
    """For 6.1b's order ticket. Rounds rather than floors: a price typed as 79830.40 must not
    become 7983039 ticks and rest one tick below where the user meant."""
    assert run_script("""
import { toTicks } from "./symbols.ts";
const qaa = { symbol_id: 1, name: "QAA", tick_size_ticks: 100, lot_size: 1 };
console.log(JSON.stringify({ ticks: toTicks(79830.40, qaa) }));
""")["ticks"] == 7983040


# --- bars: the third kind of market data --------------------------------------------------------


def test_a_repeated_bar_replaces_rather_than_appends():
    """The reconnection case, and the reason `writeBar` is not a plain push.

    A client that reconnects legitimately receives bars it has already drawn. Appending them
    would put two candles at the same x position, which reads as a data bug in the exchange
    rather than a bug in the client.
    """
    result = run_script("""
import { MarketBuffer } from "./buffer.ts";
const buffer = new MarketBuffer();
const bar = (open_ns, close) => ({
  ch: "bars:QAA:1m", seq: "1-1", open_ticks: 100, high_ticks: 120,
  low_ticks: 90, close_ticks: close, volume: 5, bar_open_ns: open_ns,
});
buffer.writeBar("QAA", "1m", bar(1000, 110));
buffer.writeBar("QAA", "1m", bar(2000, 115));
buffer.writeBar("QAA", "1m", bar(2000, 118));   // the same candle, re-delivered
const series = buffer.barSeries("QAA", "1m");
console.log(JSON.stringify({
  length: series.length,
  closes: series.map((b) => b.closeTicks),
}));
""")
    assert result["length"] == 2
    assert result["closes"] == [110, 118]


def test_the_bar_series_is_bounded():
    """This buffer lives for the length of a session; an unbounded array in it is a memory leak
    measured in hours. Same reason the tape is bounded."""
    result = run_script("""
import { BAR_LIMIT, MarketBuffer } from "./buffer.ts";
const buffer = new MarketBuffer();
for (let i = 0; i < BAR_LIMIT + 250; i += 1) {
  buffer.writeBar("QAA", "1m", {
    ch: "bars:QAA:1m", seq: `1-${i}`, open_ticks: 100, high_ticks: 120,
    low_ticks: 90, close_ticks: 100 + i, volume: 1, bar_open_ns: i * 1_000_000_000,
  });
}
const series = buffer.barSeries("QAA", "1m");
console.log(JSON.stringify({
  limit: BAR_LIMIT,
  length: series.length,
  newestKept: series[series.length - 1].closeTicks,
}));
""")
    assert result["length"] == result["limit"]
    # Trimmed from the front, so the newest bars survive — the oldest are the ones off-screen.
    assert result["newestKept"] == 100 + result["limit"] + 249


def test_bars_mark_their_symbol_changed_so_the_frame_loop_paints_them():
    """`takeChanged()` is the whole of the conflation. A bar that did not register would leave
    the chart frozen until the book happened to move."""
    assert run_script("""
import { MarketBuffer } from "./buffer.ts";
const buffer = new MarketBuffer();
buffer.writeBar("QAA", "1m", {
  ch: "bars:QAA:1m", seq: "1-1", open_ticks: 100, high_ticks: 120,
  low_ticks: 90, close_ticks: 110, volume: 1, bar_open_ns: 1_000_000_000,
});
console.log(JSON.stringify({ changed: buffer.takeChanged() }));
""")["changed"] == ["QAA"]


def test_a_burst_of_bars_between_frames_produces_one_paint():
    """The same conflation the book gets. Two hundred bars, one entry in the changed set."""
    result = run_script("""
import { MarketBuffer } from "./buffer.ts";
const buffer = new MarketBuffer();
for (let i = 0; i < 200; i += 1) {
  buffer.writeBar("QAA", "1m", {
    ch: "bars:QAA:1m", seq: `1-${i}`, open_ticks: 100, high_ticks: 120,
    low_ticks: 90, close_ticks: 100 + i, volume: 1, bar_open_ns: i * 1_000_000_000,
  });
}
console.log(JSON.stringify({ writes: buffer.writes, changed: buffer.takeChanged().length }));
""")
    assert result["writes"] == 200
    assert result["changed"] == 1


def test_a_bars_message_is_routed_into_the_buffer():
    """The client dispatches on `parseChannel`. A `bars:*` message that fell through would leave
    the chart empty with no error anywhere."""
    assert run_script("""
import { parseChannel } from "./types.ts";
console.log(JSON.stringify({ parsed: parseChannel("bars:QAA:1m") }));
""")["parsed"] == {"kind": "bars", "symbol": "QAA", "tier": "1m"}


# --- the rendering rule still holds --------------------------------------------------------------


def test_the_market_data_modules_still_do_not_import_react():
    """6.1a added bars to the buffer and a symbol table beside it. Neither may reach React —
    the rule is the whole design, so it is re-asserted rather than assumed to have survived."""
    for module in ("buffer.ts", "symbols.ts", "frameLoop.ts", "client.ts"):
        source = code_of(STREAM / module)
        assert 'from "react' not in source and "from 'react" not in source, module


def test_the_panels_paint_through_refs_rather_than_state():
    """A panel that held book data in `useState` would re-render on every message and undo the
    entire arrangement. The panels take a `register` callback and write through refs instead."""
    for name in ("BookPanel.tsx", "TapePanel.tsx"):
        source = code_of(COMPONENTS / name)
        assert "useState" not in source, f"{name} puts market data in React state"
        assert "useRef" in source and "register(" in source, name


def test_the_screen_owns_exactly_one_frame_loop():
    """`takeChanged()` clears the changed set, so the first loop to run consumes the change and
    any others paint nothing. One loop is the only correct arrangement, not a preference."""
    source = code_of(SRC / "screens" / "Trading.tsx")
    assert source.count("startFrameLoop(") == 1
    for name in ("BookPanel.tsx", "TapePanel.tsx", "Chart.tsx"):
        assert "startFrameLoop" not in code_of(COMPONENTS / name), name


def test_the_screen_does_not_poll_rest():
    """Task 6.1's Boundaries: the REST endpoints exist solely for re-synchronisation after a
    gap. The symbol table is fetched once at startup, which is a different thing — a listing is
    a restart, not a live value."""
    source = code_of(SRC / "screens" / "Trading.tsx")
    assert "setInterval" not in source
    assert "fetch(" not in source


def test_still_three_screens():
    """Open Issue 014 §11.1 fixes the count in advance. 6.1a builds one of the three out; it
    does not add a fourth."""
    assert (SRC / "routes.ts").read_text().count("id: \"") == 3


def test_two_bar_widths_are_separate_series():
    """`bars:QAA:1m` and `bars:QAA:1h` are different series of one instrument.

    The router used to discard the width, so both would have landed in one array — and because
    the de-duplication in `writeBar` compares only against the *last* element, alternating
    widths would both append and the chart would draw two timeframes as one line. Latent while
    only one width is subscribed; wrong the moment a second is, which 7.1's backtester will do.
    """
    result = run_script("""
import { MarketBuffer } from "./buffer.ts";
const buffer = new MarketBuffer();
const bar = (open_ns, close) => ({
  seq: "1-1", open_ticks: 100, high_ticks: 120,
  low_ticks: 90, close_ticks: close, volume: 5, bar_open_ns: open_ns,
});
// The same bucket start on two widths — which is exactly what happens on the wire, because
// both builders cut on the same timestamp.
buffer.writeBar("QAA", "1m", bar(1000, 110));
buffer.writeBar("QAA", "1h", bar(1000, 999));
console.log(JSON.stringify({
  minute: buffer.barSeries("QAA", "1m").map((b) => b.closeTicks),
  hour: buffer.barSeries("QAA", "1h").map((b) => b.closeTicks),
}));
""")
    assert result["minute"] == [110], "the hour bar overwrote the minute series"
    assert result["hour"] == [999]


def test_the_client_routes_a_bar_onto_its_own_width():
    """End to end through `parseChannel`: the width in the channel name is what the buffer is
    keyed by, so a message on `bars:QAA:1h` must not appear in the `1m` series."""
    result = run_script("""
import { MarketBuffer } from "./buffer.ts";
import { StreamClient } from "./client.ts";
import { MockStreamSocket } from "./mock.ts";

const buffer = new MarketBuffer();
let socket = null;
const client = new StreamClient({
  url: "/stream",
  channels: ["bars:QAA:1m", "bars:QAA:1h"],
  buffer,
  socketFactory: () => { socket = new MockStreamSocket({ symbols: ["QAA"] }); return socket; },
});
client.connect();
socket.open();
const bar = (ch, close) => JSON.stringify({
  ch, seq: "1-1", open_ticks: 100, high_ticks: 120, low_ticks: 90,
  close_ticks: close, volume: 5, bar_open_ns: 1000,
});
socket.onmessage({ data: bar("bars:QAA:1m", 110) });
socket.onmessage({ data: bar("bars:QAA:1h", 999) });
console.log(JSON.stringify({
  minute: buffer.barSeries("QAA", "1m").map((b) => b.closeTicks),
  hour: buffer.barSeries("QAA", "1h").map((b) => b.closeTicks),
}));
""")
    assert result["minute"] == [110]
    assert result["hour"] == [999]
