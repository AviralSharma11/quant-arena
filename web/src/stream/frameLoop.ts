/**
 * The render loop — the browser half of conflation.
 *
 * Receiving and rendering are decoupled. The socket writes into `MarketBuffer` at whatever rate
 * the server sends; this loop reads it once per animation frame and paints whatever is current.
 * If a burst arrives between two frames, the buffer is simply overwritten and the next frame
 * paints the latest value — the intermediate ones are lost, harmlessly, because every book
 * message is a complete snapshot (Open Issue 014 §14a).
 *
 * Two rates that never have to match:
 *
 * | Layer | Rate |
 * |---|---|
 * | server fan-out | 20 Hz |
 * | browser receive | 20 Hz, straight into a plain object |
 * | browser render | ≤60 Hz, whatever the display and the tab allow |
 *
 * `requestAnimationFrame` is doing more than pacing here. The browser stops calling it in a
 * background tab, so a hidden trading tab costs nothing at all while the buffer keeps taking
 * messages — and the frame after the tab returns paints current prices, not a queue of stale
 * ones. A `setInterval` would keep painting into a tab nobody is looking at.
 *
 * No React import, by rule. See `buffer.ts`.
 */

import type { MarketBuffer } from "./buffer.ts";

export interface FrameLoopOptions {
  buffer: MarketBuffer;
  /** Called at most once per frame, with the symbols that changed. Never called with none. */
  paint: (symbols: string[], buffer: MarketBuffer) => void;
  /** Injectable so a test can drive frames by hand instead of waiting for a display. */
  requestFrame?: (callback: () => void) => number;
  cancelFrame?: (handle: number) => void;
}

/** Starts the loop. Returns a function that stops it — the cleanup a React effect returns. */
export function startFrameLoop(options: FrameLoopOptions): () => void {
  const request =
    options.requestFrame ??
    ((callback: () => void) => globalThis.requestAnimationFrame(() => callback()));
  const cancel = options.cancelFrame ?? ((handle: number) => globalThis.cancelAnimationFrame(handle));

  let handle = 0;
  let stopped = false;

  const tick = (): void => {
    if (stopped) return;
    const changed = options.buffer.takeChanged();
    // Nothing changed: no paint at all. A loop that painted every frame regardless would do
    // sixty pointless layouts a second on a market that had not moved.
    if (changed.length > 0) options.paint(changed, options.buffer);
    handle = request(tick);
  };

  handle = request(tick);

  return () => {
    stopped = true;
    cancel(handle);
  };
}
