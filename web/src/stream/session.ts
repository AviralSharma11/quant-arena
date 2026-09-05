/**
 * Wires the stream into the app: one buffer, one client, one place the mock is chosen.
 *
 * Kept out of `App.tsx` so the component stays about rendering and this stays about transport.
 * It is also the single call site Task 5.2b changes — swapping the mock for the real socket is
 * one branch here, and nothing in the client, the buffer or the loop knows the difference.
 */

import { MarketBuffer } from "./buffer.ts";
import { StreamClient } from "./client.ts";
import { MockStreamSocket } from "./mock.ts";
import type { ConnectionState } from "./types.ts";

/** The symbols subscribed to. Task 5.1 replaces the provisional pair; `GET /symbols` is the
 *  real source, which Task 6.1 will read when the trading screen needs tick sizes. */
export const STREAM_SYMBOLS = ["QAA", "QAB"];

/** @deprecated Kept because 5.4c's tests import it by this name. */
export const MOCK_SYMBOLS = STREAM_SYMBOLS;

/**
 * False from Task 5.2b, which serves `/stream` from `services/fanout`.
 *
 * A build-time flag rather than a runtime probe: a client that tried the real socket and fell
 * back to a mock on failure would silently show invented prices whenever the exchange was down,
 * which is the one thing Open Issue 014 §14e says a trading interface must never do. Flipping
 * it back is how the frontend is developed with no stack running, and it is what the mock is
 * still for — that, and being the only practical way to *make* a sequence gap happen.
 */
export const USE_MOCK_STREAM = false;

export interface StreamSession {
  buffer: MarketBuffer;
  client: StreamClient;
  /** Present only while mocked. Drives frames, and can be told to skip a sequence number. */
  mock: MockStreamSocket | null;
  stop: () => void;
}

export function startStreamSession(options: {
  onState: (state: ConnectionState) => void;
  onResync?: () => void | Promise<void>;
}): StreamSession {
  const buffer = new MarketBuffer();
  const channels = STREAM_SYMBOLS.flatMap((symbol) => [
    `book:${symbol}:l2`,
    `tape:${symbol}`,
  ]);

  let mock: MockStreamSocket | null = null;
  const client = new StreamClient({
    url: "/stream",
    channels,
    buffer,
    onState: options.onState,
    onResync: options.onResync,
    socketFactory: USE_MOCK_STREAM
      ? () => {
          mock = new MockStreamSocket({ symbols: STREAM_SYMBOLS });
          // The handshake completes on the next turn of the event loop, so the caller has
          // finished wiring `onopen` before it fires — which is how a real socket behaves.
          const socket = mock;
          queueMicrotask(() => socket.open());
          return socket;
        }
      : undefined,
  });

  client.connect();

  // The mock produces one tick of traffic per animation frame, so a demo runs at the display's
  // rate rather than a timer's — and stops entirely in a background tab, exactly as the real
  // 20 Hz feed's rendering does.
  let frame = 0;
  const pump = () => {
    mock?.emitTick();
    frame = requestAnimationFrame(pump);
  };
  if (USE_MOCK_STREAM) frame = requestAnimationFrame(pump);

  return {
    buffer,
    client,
    get mock() {
      return mock;
    },
    stop: () => {
      cancelAnimationFrame(frame);
      client.close();
    },
  };
}
