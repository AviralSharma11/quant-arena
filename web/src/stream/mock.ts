/**
 * A fake socket that speaks the real wire — the stand-in until Task 5.2b.
 *
 * Task 5.4's Dependencies line puts this here explicitly: 5.4c "works against the mock
 * WebSocket server until 5.2b lands." It is not only a placeholder, though. It is the only
 * practical way to *prove* gap detection: a real server will never conveniently skip a
 * sequence number, and this one does it on demand.
 *
 * No network, no `ws` dependency, no second process. It implements `SocketLike` — which is
 * narrow precisely so this file can be short — and drives itself from an injected timer, so a
 * test advances it by hand rather than sleeping.
 *
 * The frames are the shapes `services/fanout/messages.py` builds, down to key order. If the two
 * drift, the client is being developed against a wire that does not exist; the pytest suite
 * asserts the fan-out side against the same contract, so both are pinned to the document rather
 * than to each other.
 */

import type { SocketLike } from "./client.ts";

/** Deterministic, so a seeded run replays identically — the same discipline as the bots. */
function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export interface MockServerOptions {
  symbols?: string[];
  seed?: number;
  /** Starting fair value in ticks, per symbol. */
  startTicks?: number;
  depth?: number;
}

/**
 * A socket that emits contract-shaped frames when told to.
 *
 * Nothing happens on a timer of its own: `emitTick()` produces one 20 Hz tick's worth of
 * traffic. That keeps the mock deterministic and lets a demo drive it from the same
 * `requestAnimationFrame` the renderer uses, or a test drive it in a loop.
 */
export class MockStreamSocket implements SocketLike {
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: ((error: unknown) => void) | null = null;

  readonly sent: string[] = [];
  subscribedChannels: string[] = [];

  private readonly symbols: string[];
  private readonly depth: number;
  private readonly random: () => number;
  private readonly fair = new Map<string, number>();
  private ms = 1_700_000_000_000;
  private ordinal = 0;
  private privateSeq = 0;
  private skipPrivate = 0;
  private closed = false;

  constructor(options: MockServerOptions = {}) {
    this.symbols = options.symbols ?? ["QAA", "QAB"];
    this.depth = options.depth ?? 10;
    this.random = mulberry32(options.seed ?? 20260904);
    for (const symbol of this.symbols) {
      this.fair.set(symbol, options.startTicks ?? 1_000);
    }
  }

  // -- SocketLike --------------------------------------------------------------------------------

  send(data: string): void {
    this.sent.push(data);
    try {
      const frame = JSON.parse(data) as { op?: string; channels?: string[] };
      if (frame.op === "subscribe" && Array.isArray(frame.channels)) {
        this.subscribedChannels = frame.channels;
      }
    } catch {
      // A client sending malformed JSON is a client bug; the real server answers with an
      // `unknown_channel` error rather than closing, and there is nothing to assert here.
    }
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.onclose?.();
  }

  /** Call after wiring the handlers, to complete the handshake. */
  open(): void {
    this.onopen?.();
  }

  /** Simulate the connection dropping from the far end. */
  drop(): void {
    this.close();
  }

  // -- emitting ----------------------------------------------------------------------------------

  /** One 20 Hz tick: a book snapshot per symbol, and a print on some of them. */
  emitTick(): void {
    for (const symbol of this.symbols) {
      this.emitBook(symbol);
      if (this.random() < 0.4) this.emitTrade(symbol);
    }
    this.ms += 50;
  }

  /**
   * Drop the next `count` private sequence numbers before sending.
   *
   * The whole reason this file exists. A gap has to be *made* to happen, and only a mock will
   * make one on request.
   */
  skipPrivateSequences(count = 1): void {
    this.skipPrivate += count;
  }

  emitPrivate(type: string, fields: Record<string, unknown> = {}): void {
    this.privateSeq += 1 + this.skipPrivate;
    this.skipPrivate = 0;
    this.deliver({
      ch: "private",
      type,
      seq: String(this.privateSeq),
      ts_ns: this.ms * 1_000_000,
      ...fields,
    });
  }

  emitError(code: string, detail: string): void {
    this.deliver({ ch: "error", code, detail });
  }

  private emitBook(symbol: string): void {
    const fair = this.step(symbol);
    const half = Math.max(1, Math.round(fair * 0.0025));
    const bids: [number, number][] = [];
    const asks: [number, number][] = [];
    for (let level = 0; level < this.depth; level += 1) {
      bids.push([fair - half - level, 40 + Math.round(this.random() * 160)]);
      asks.push([fair + half + level, 40 + Math.round(this.random() * 160)]);
    }
    this.deliver({
      ch: `book:${symbol}:l2`,
      seq: this.nextSeq(),
      ts_ns: this.ms * 1_000_000,
      bids,
      asks,
    });
  }

  private emitTrade(symbol: string): void {
    const fair = this.fair.get(symbol) ?? 1_000;
    const aggressorSide = this.random() < 0.5 ? 1 : 2;
    this.deliver({
      ch: `tape:${symbol}`,
      seq: this.nextSeq(),
      ts_ns: this.ms * 1_000_000,
      price_ticks: fair + (aggressorSide === 1 ? 1 : -1),
      qty: 1 + Math.floor(this.random() * 9),
      aggressor_side: aggressorSide,
    });
  }

  private step(symbol: string): number {
    const previous = this.fair.get(symbol) ?? 1_000;
    const next = Math.max(10, previous + Math.round((this.random() - 0.5) * 6));
    this.fair.set(symbol, next);
    return next;
  }

  /** The Redis stream id shape. The ordinal advances across channels, which is exactly why a
   *  jump on any one channel is normal — see `gaps.ts`. */
  private nextSeq(): string {
    this.ordinal += 1;
    return `${this.ms}-${this.ordinal}`;
  }

  private deliver(payload: unknown): void {
    if (this.closed) return;
    this.onmessage?.({ data: JSON.stringify(payload) });
  }
}
