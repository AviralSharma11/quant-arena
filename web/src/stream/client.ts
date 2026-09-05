/**
 * The WebSocket client: one connection, all channels, and the two recoveries.
 *
 * Open Issue 014 §14e divides recovery along the same line as everything else in this system:
 *
 * | Stream | On reconnect |
 * |---|---|
 * | market data | re-subscribe and wait. The next conflated snapshot is complete, so recovery is automatic and needs no special handling |
 * | private data | compare the per-user sequence; on a gap, re-fetch open orders and the portfolio over REST |
 *
 * That asymmetry is not an accident — it is the droppable/non-droppable distinction that shaped
 * the server, surfacing again on the client.
 *
 * ## Everything time-shaped is injectable
 *
 * The socket, the timer and the random source all come in through options. Not for purity: it
 * is the only way to test a reconnection policy without a test that sleeps for eight seconds
 * and is flaky anyway. `mock.ts` supplies a socket that never touches the network, which is
 * also the only practical way to *prove* gap detection — a real server will not conveniently
 * skip a sequence number, and a mock will do it on demand.
 */

import type { MarketBuffer } from "./buffer.ts";
import { SequenceTracker } from "./gaps.ts";
import type {
  BookMessage,
  ConnectionState,
  ErrorMessage,
  PrivateMessage,
  StreamMessage,
  TapeMessage,
} from "./types.ts";
import { parseChannel } from "./types.ts";

/** The part of `WebSocket` this client uses. Narrow on purpose, so a mock is a few lines. */
export interface SocketLike {
  send(data: string): void;
  close(): void;
  onopen: (() => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
  onclose: (() => void) | null;
  onerror: ((error: unknown) => void) | null;
}

/**
 * Backoff between reconnection attempts, in milliseconds, then the last value repeats.
 *
 * Jittered, because Task 5.2's first success criterion is two hundred concurrent clients: a
 * server restart drops all of them at once, and an unjittered backoff has every one of them
 * return in lockstep at 250 ms, then again at 500 — which is a thundering herd arriving exactly
 * when the server is least able to absorb it.
 */
export const BACKOFF_MS = [250, 500, 1_000, 2_000, 5_000];
export const BACKOFF_JITTER = 0.25;

export interface StreamClientOptions {
  url: string;
  channels: string[];
  buffer: MarketBuffer;
  socketFactory?: (url: string) => SocketLike;
  onState?: (state: ConnectionState) => void;
  onPrivate?: (message: PrivateMessage) => void;
  /** Called once per detected gap. Wire this to `GET /orders/open` and `GET /portfolio`. */
  onResync?: () => void | Promise<void>;
  onError?: (message: ErrorMessage) => void;
  setTimer?: (callback: () => void, ms: number) => number;
  clearTimer?: (handle: number) => void;
  random?: () => number;
}

export class StreamClient {
  readonly tracker = new SequenceTracker();
  state: ConnectionState = "closed";

  /** Counters, for the connection indicator and for tests. */
  connects = 0;
  subscribes = 0;
  resyncs = 0;
  attempt = 0;

  private readonly options: StreamClientOptions;
  private socket: SocketLike | null = null;
  private timer: number | null = null;
  private closedByUs = false;
  private resyncInFlight = false;

  constructor(options: StreamClientOptions) {
    this.options = options;
  }

  // -- lifecycle -------------------------------------------------------------------------------

  connect(): void {
    this.closedByUs = false;
    this.open();
  }

  close(): void {
    this.closedByUs = true;
    this.cancelTimer();
    this.socket?.close();
    this.socket = null;
    this.setState("closed");
  }

  private open(): void {
    this.setState(this.attempt === 0 ? "connecting" : "reconnecting");

    const factory =
      this.options.socketFactory ??
      ((url: string) => new WebSocket(url) as unknown as SocketLike);
    const socket = factory(this.options.url);
    this.socket = socket;

    socket.onopen = () => {
      this.connects += 1;
      this.attempt = 0;
      // Nothing is compared across a reconnect: the server's position is unknown, and the
      // first message back would otherwise look like a gap on every channel at once.
      this.tracker.reset();
      this.subscribe();
      this.setState("connected");
    };

    socket.onmessage = (event) => this.receive(event.data);
    socket.onclose = () => this.scheduleReconnect();
    socket.onerror = () => {
      // Left to `onclose`. A browser fires error then close for the same failure, and acting
      // on both schedules two reconnections for one drop.
    };
  }

  /** Sent on **every** open, not only the first — a reconnected socket is a new subscription
   *  as far as the server is concerned (Task 5.4, Success Criterion 2). */
  private subscribe(): void {
    this.socket?.send(
      JSON.stringify({ op: "subscribe", channels: this.options.channels }),
    );
    this.subscribes += 1;
  }

  private scheduleReconnect(): void {
    this.socket = null;
    if (this.closedByUs) return;

    this.setState("reconnecting");
    const base = BACKOFF_MS[Math.min(this.attempt, BACKOFF_MS.length - 1)];
    const random = this.options.random ?? Math.random;
    const delay = Math.round(base * (1 + BACKOFF_JITTER * (random() * 2 - 1)));
    this.attempt += 1;

    const setTimer =
      this.options.setTimer ??
      ((callback: () => void, ms: number) => globalThis.setTimeout(callback, ms) as unknown as number);
    this.timer = setTimer(() => {
      this.timer = null;
      this.open();
    }, delay);
  }

  private cancelTimer(): void {
    if (this.timer === null) return;
    const clearTimer =
      this.options.clearTimer ?? ((handle: number) => globalThis.clearTimeout(handle));
    clearTimer(this.timer);
    this.timer = null;
  }

  // -- messages --------------------------------------------------------------------------------

  private receive(raw: string): void {
    let message: StreamMessage;
    try {
      message = JSON.parse(raw) as StreamMessage;
    } catch {
      // A frame that is not JSON is a server fault, and dropping the connection over it would
      // turn one bad frame into an outage. Ignored; the next frame repairs the book anyway.
      return;
    }

    const channel = parseChannel(message.ch);

    if (channel.kind === "error") {
      const error = message as ErrorMessage;
      this.options.onError?.(error);
      // `halted` is the exchange saying it cannot durably record orders (Open Issue 003 §8.5).
      // The socket is fine; the venue is not, and the indicator must say so rather than show
      // a confident "connected" over prices nobody can trade on.
      //
      // `resumed` is 5.2b's addition to §3.6, which enumerates only failures and so gave a
      // halt no way to end. Without it the indicator could be cleared only by reconnecting,
      // and a halt lifts on its own within one watchdog interval — market data keeps flowing
      // throughout, so there is nothing else to infer it from. Pending Dev A's sign-off.
      if (error.code === "halted") this.setState("halted");
      else if (error.code === "resumed" && this.state === "halted") this.setState("connected");
      return;
    }

    const verdict = this.tracker.observe(message.ch, (message as StreamMessage & { seq: string }).seq);
    if (verdict === "duplicate") return;

    if (channel.kind === "private") {
      if (verdict === "gap") void this.resync();
      this.options.onPrivate?.(message as PrivateMessage);
      return;
    }

    // A gap here is ignored on purpose: the next snapshot is complete, so the client is
    // already correct again. §3.5 says so explicitly.
    if (channel.symbol === null) return;
    if (channel.kind === "book") {
      this.options.buffer.writeBook(channel.symbol, message as BookMessage);
    } else if (channel.kind === "tape") {
      this.options.buffer.writeTrade(channel.symbol, message as TapeMessage);
    }
    // `bars` are consumed by the chart in Task 6.1 and are not buffered here yet.
  }

  /**
   * Re-fetch open orders and the portfolio, once.
   *
   * Guarded against overlap. A burst of private messages after a reconnect can register
   * several gaps in a few milliseconds, and firing a REST round trip for each would hit the
   * gateway hardest at the moment it just came back — which is also when the rate limiter is
   * least forgiving.
   */
  private async resync(): Promise<void> {
    if (this.resyncInFlight) return;
    this.resyncInFlight = true;
    this.resyncs += 1;
    try {
      await this.options.onResync?.();
    } finally {
      this.resyncInFlight = false;
    }
  }

  private setState(state: ConnectionState): void {
    if (this.state === state) return;
    this.state = state;
    this.options.onState?.(state);
  }
}
