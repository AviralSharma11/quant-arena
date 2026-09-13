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

import {
  CandlestickSeries,
  HistogramSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import { formatTicksGrouped } from "../format";
import type { MarketBuffer } from "../stream/buffer";
import { CHART_BAR_WIDTH, type Symbol } from "../stream/symbols";

/** The canvas cannot read CSS custom properties, so these mirror `--up`, `--down` and the
 *  hairline tokens in `index.css`. */
const UP = "#4edea3";
const DOWN = "#ff8f86";
const UP_VOLUME = "rgba(78, 222, 163, 0.35)";
const DOWN_VOLUME = "rgba(255, 143, 134, 0.35)";
const HAIRLINE = "#1e2638";
const AXIS_TEXT = "#94a3b8";

export interface ChartProps {
  symbol: Symbol;
  buffer: MarketBuffer;
  register: (symbolName: string, paint: () => void) => () => void;
}

export function Chart({ symbol, buffer, register }: ChartProps) {
  const container = useRef<HTMLDivElement | null>(null);
  const lastPrice = useRef<HTMLSpanElement | null>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);

  useEffect(() => {
    const element = container.current;
    if (element === null) return;

    const api = createChart(element, {
      autoSize: true,
      layout: {
        background: { color: "transparent" },
        textColor: AXIS_TEXT,
        fontFamily: "'JetBrains Mono', ui-monospace, monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "rgba(30, 38, 56, 0.6)" },
        horzLines: { color: "rgba(30, 38, 56, 0.6)" },
      },
      rightPriceScale: { borderColor: HAIRLINE },
      timeScale: { borderColor: HAIRLINE, timeVisible: true, secondsVisible: true },
    });
    const candles = api.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
      borderVisible: false,
      priceFormat: {
        type: "price",
        // The symbol's own scale, from `GET /symbols`. Four different tick sizes are listed
        // (Task 5.1), so a chart that assumed two decimal places would be wrong on six of ten.
        precision: Math.max(0, Math.round(Math.log10(symbol.tick_size_ticks))),
        minMove: 1 / symbol.tick_size_ticks,
      },
    });
    candles.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.28 } });

    // Volume on its own overlay scale along the bottom fifth, under the candles. `volume` is a
    // field of every bar on the wire (§3.3), so this draws nothing the stream does not carry.
    const volume = api.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

    chart.current = api;
    series.current = candles;

    const paint = () => {
      // The same width the session subscribes to. The buffer keys bar series by symbol *and*
      // width, so asking for a width nobody subscribed to correctly returns nothing.
      const bars = buffer.barSeries(symbol.name, CHART_BAR_WIDTH);

      if (lastPrice.current !== null) {
        const last = buffer.lastTrade(symbol.name)?.priceTicks ?? bars[bars.length - 1]?.closeTicks;
        lastPrice.current.textContent =
          last === undefined ? "—" : formatTicksGrouped(last, symbol, { currency: true });
      }

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
      volume.setData(
        bars.map((bar) => ({
          time: Math.floor(bar.barOpenNs / 1_000_000_000) as never,
          value: bar.volume,
          color: bar.closeTicks >= bar.openTicks ? UP_VOLUME : DOWN_VOLUME,
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
    <section className="panel chart-panel" aria-label={`Price chart for ${symbol.name}`}>
      <header className="panel-head">
        <div className="chart-title">
          <h3 className="chart-symbol">{symbol.name}</h3>
          <span className="chart-last num" ref={lastPrice}>
            &mdash;
          </span>
        </div>
        {/* One candle per completed bar. At the replay clock's one real second to one simulated
            minute, a `1m` bar closes about once a second. */}
        <span
          className="panel-meta"
          title="At the replay clock's one real second to one simulated minute, a 1m bar closes about once a second."
        >
          {CHART_BAR_WIDTH} candles · volume
        </span>
      </header>
      <div className="chart" ref={container} />
    </section>
  );
}
