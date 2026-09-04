/**
 * Sequence tracking, and the one channel where a gap actually means something.
 *
 * `contracts/v1/rest_and_ws.md` §3.5 says the client tracks the last `seq` per channel and that
 * a jump means messages were lost. That is true of exactly one channel, and the reason is worth
 * writing down because it is easy to build the wrong thing here.
 *
 * ## Why a "jump" is normal on market data
 *
 * `seq` on a market-data channel is the **Redis stream id** — `<ms>-<ord>` — and a channel
 * carries only *some* of the stream's records. So `…-5` followed by `…-9` on `book:QAA:l2` is
 * entirely normal: 6, 7 and 8 went to other symbols, or were records this channel does not
 * carry at all. Treating that as loss would fire a re-synchronisation on almost every message.
 *
 * It also does not matter. §3.5 says a book gap is self-healing and should be ignored, because
 * every book message is a complete snapshot. So on these channels the only thing worth
 * checking is **monotonicity** — a *decreasing* seq is genuinely out-of-order delivery, which
 * is a real fault and is surfaced rather than silently accepted.
 *
 * ## Why the private channel is different
 *
 * Open Issue 006 §7c: private messages carry a **per-user sequence number**, precisely so a gap
 * is detectable. That counter is dense — every message this user is party to increments it by
 * one — so `n` followed by `n + 2` means one was genuinely lost, and the client re-fetches
 * `GET /orders/open` and `GET /portfolio` once (§14e).
 *
 * The asymmetry is not an accident. It is the same distinction between droppable and
 * non-droppable data that shaped the server: market data may be dropped because the next
 * message supersedes it; a lost fill is a user seeing wrong state.
 *
 * **Open with Dev A:** §3.4's private example shows `"seq": "1693526400000-9"`, which looks
 * like a stream id rather than a dense counter — and stream ids are not dense per user either,
 * so read literally no private gap is detectable and the criterion cannot be met. This module
 * takes the reading that makes §3.5 true: on `private`, `seq` is the per-user counter. The
 * decision is isolated in `privateSequence()` so 5.2b can change it in one place.
 */

import { parseChannel } from "./types.ts";

export type SequenceVerdict = "ok" | "duplicate" | "out_of_order" | "gap";

/** `<ms>-<ord>` as a pair. Compared numerically, never as strings: `"9-0"` sorts after
 *  `"10-0"` lexicographically, which would make every id past the ninth look out of order. */
export function parseStreamId(seq: string): [number, number] {
  const dash = seq.indexOf("-");
  if (dash < 0) return [Number(seq) || 0, 0];
  return [Number(seq.slice(0, dash)) || 0, Number(seq.slice(dash + 1)) || 0];
}

export function compareStreamId(a: string, b: string): number {
  const [aMs, aOrd] = parseStreamId(a);
  const [bMs, bOrd] = parseStreamId(b);
  if (aMs !== bMs) return aMs < bMs ? -1 : 1;
  if (aOrd !== bOrd) return aOrd < bOrd ? -1 : 1;
  return 0;
}

/**
 * The private channel's dense counter, extracted from whatever `seq` turns out to be.
 *
 * The single place the §3.4 ambiguity lives. Today it reads a bare integer and falls back to
 * the ordinal half of a stream id, so the client behaves sensibly under either reading while
 * the question is open.
 */
export function privateSequence(seq: string): number {
  if (seq.indexOf("-") < 0) return Number(seq) || 0;
  return parseStreamId(seq)[1];
}

export class SequenceTracker {
  private readonly lastByChannel = new Map<string, string>();
  private lastPrivate: number | null = null;

  /** Gaps observed on the private channel. Read by tests and by a log line. */
  privateGaps = 0;
  outOfOrder = 0;

  observe(ch: string, seq: string): SequenceVerdict {
    return parseChannel(ch).kind === "private"
      ? this.observePrivate(seq)
      : this.observeMarketData(ch, seq);
  }

  private observePrivate(seq: string): SequenceVerdict {
    const next = privateSequence(seq);
    const previous = this.lastPrivate;
    this.lastPrivate = next;

    if (previous === null) return "ok";
    if (next === previous) return "duplicate";
    if (next < previous) {
      this.outOfOrder += 1;
      return "out_of_order";
    }
    if (next > previous + 1) {
      this.privateGaps += 1;
      return "gap";
    }
    return "ok";
  }

  private observeMarketData(ch: string, seq: string): SequenceVerdict {
    const previous = this.lastByChannel.get(ch);
    this.lastByChannel.set(ch, seq);
    if (previous === undefined) return "ok";

    const order = compareStreamId(previous, seq);
    if (order === 0) return "duplicate";
    if (order > 0) {
      this.outOfOrder += 1;
      return "out_of_order";
    }
    // Ahead of the last one, by however much. Not a gap: this channel never carried the
    // records in between. See the module docstring.
    return "ok";
  }

  /** After a reconnect the server's position is unknown, so nothing is compared across it —
   *  otherwise the first message back would look like a gap on every single channel. */
  reset(): void {
    this.lastByChannel.clear();
    this.lastPrivate = null;
  }
}
