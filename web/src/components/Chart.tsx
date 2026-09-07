/**
 * The candlestick chart — TradingView Lightweight Charts, fed from the bar buffer.
 *
 * The library owns a canvas and its own imperative API, which suits this codebase's rendering
 * rule exactly: the chart is created once in an effect and updated by method call from the
 * frame loop. Bars never enter React state, so the same guarantee the book and tape have holds
 * here — no re-render on market data.
 *
 * ## Two decisions worth recording
 *
 * **`setData` on a whole series, not `update` per bar.** `update()` is the library's fast path
 * and appends one candle, but it rejects a bar older than the last one it saw — and after a
 * reconnect the client legitimately replays bars it has already drawn. Rebuilding from the
 * buffer is idempotent and costs a few hundred points once a frame, only on frames where a bar
 * actually changed. Correct under reconnection beats marginally cheaper.
 *
 * **Prices are divided for display, and only here.** The series carries real numbers because a
 * chart axis is presentation, which is the one layer Open Issue 016 permits a float. Nothing
 * read off this chart is ever sent back to the gateway.
 */

import { CandlestickSeries, createChart, type IChartApi, type ISeriesApi } from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { MarketBuffer } from "../stream/buffer";
import type { Symbol } from "../stream/symbols";

export interface ChartProps {
  symbol: Symbol;
  buffer: MarketBuffer;
  register: (symbolName: string, paint: () => void) => () => void;
}

export function Chart({ symbol, buffer, register }: ChartProps) {
  const container = useRef<HTMLDivElement | null>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);

  useEffect(() => {
    const element = container.current;
    if (element === null) return;

    const api = createChart(element, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: "#b9c2d0" },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.04)" },
        horzLines: { color: "rgba(255,255,255,0.04)" },
      },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.1)" },
      timeScale: { borderColor: "rgba(255,255,255,0.1)", timeVisible: true, secondsVisible: true },
    });
    const candles = api.addSeries(CandlestickSeries, {
      upColor: "#2e9e6b",
      downColor: "#c0455a",
      wickUpColor: "#2e9e6b",
      wickDownColor: "#c0455a",
      borderVisible: false,
      priceFormat: {
        type: "price",
        // The symbol's own scale, from `GET /symbols`. Four different tick sizes are listed
        // (Task 5.1), so a chart that assumed two decimal places would be wrong on six of ten.
        precision: Math.max(0, Math.round(Math.log10(symbol.tick_size_ticks))),
        minMove: 1 / symbol.tick_size_ticks,
      },
    });

    chart.current = api;
    series.current = candles;

    const paint = () => {
      const bars = buffer.barSeries(symbol.name);
      if (bars.length === 0) return;
      candles.setData(
        bars.map((bar) => ({
          // Lightweight Charts wants whole seconds. `bar_open_ns` is the gateway's stamp.
          time: Math.floor(bar.barOpenNs / 1_000_000_000) as never,
          open: bar.openTicks / symbol.tick_size_ticks,
          high: bar.highTicks / symbol.tick_size_ticks,
          low: bar.lowTicks / symbol.tick_size_ticks,
          close: bar.closeTicks / symbol.tick_size_ticks,
        })),
      );
    };

    paint();
    const unregister = register(symbol.name, paint);

    return () => {
      unregister();
      api.remove();
      chart.current = null;
      series.current = null;
    };
  }, [symbol, buffer, register]);

  return (
    <section className="chart-panel" aria-label={`Price chart for ${symbol.name}`}>
      <h3>Chart</h3>
      <div className="chart" ref={container} />
      <p className="hint">
        One candle per completed bar. At the replay clock&rsquo;s one real second to one
        simulated minute, a <code>1m</code> bar closes about once a second.
      </p>
    </section>
  );
}
