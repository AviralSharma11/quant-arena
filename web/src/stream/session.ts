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
import { channelsFor, type Symbol } from "./symbols.ts";
import type { ConnectionState } from "./types.ts";

/**
 * The symbols the session subscribes to are **passed in**, resolved from `GET /symbols`.
 *
 * They used to be the constant `["QAA", "QAB"]`, which was right while two provisional symbols
 * were all that existed and became silently wrong the moment Task 5.1 listed ten with four
 * different tick sizes. §2.3 makes that endpoint the only source of names and scales, so the
 * client resolves them and never holds its own copy.
 */

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
  symbols: readonly Symbol[];
  onState: (state: ConnectionState) => void;
  onResync?: () => void | Promise<void>;
}): StreamSession {
  const buffer = new MarketBuffer();
  // Book, tape and bars for every listed symbol. Bars are new in 6.1a — the chart needs them,
  // and nothing subscribed to them before there was a chart to draw.
  const channels = channelsFor(options.symbols);
  const names = options.symbols.map((symbol) => symbol.name);

  let mock: MockStreamSocket | null = null;
  const client = new StreamClient({
    url: "/stream",
    channels,
    buffer,
    onState: options.onState,
    onResync: options.onResync,
    socketFactory: USE_MOCK_STREAM
      ? () => {
          mock = new MockStreamSocket({ symbols: names });
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
