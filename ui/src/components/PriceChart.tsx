import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import { snapToCandle, sma, TF_MS } from "../lib/indicators";
import type { Candle, JournalEntry, Signal } from "../types";

export interface Overlays {
  sma20: boolean;
  sma50: boolean;
  volume: boolean;
}

interface Props {
  candles: Candle[];
  tf: string;
  signals: Signal[]; // уже отфильтрованы по тикеру
  trades: JournalEntry[]; // сделки из журнала по тикеру
  overlays: Overlays;
  theme: string; // меняется → перекрашиваем график
  resetKey: string; // тикер+таймфрейм: при смене — показать весь ряд
}

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const ts = (ms: number) => (ms / 1000) as UTCTimestamp;

function palette() {
  return {
    text: css("--muted"),
    grid: css("--border"),
    up: css("--up"),
    down: css("--down"),
    accent: css("--accent"),
    warn: css("--warn"),
  };
}

export function PriceChart({ candles, tf, signals, trades, overlays, theme, resetKey }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<{
    candle: ISeriesApi<"Candlestick">;
    volume: ISeriesApi<"Histogram">;
    sma20: ISeriesApi<"Line">;
    sma50: ISeriesApi<"Line">;
    markers: ISeriesMarkersPluginApi<Time>;
  } | null>(null);
  const lastKey = useRef("");

  // создаём график один раз
  useEffect(() => {
    if (!box.current) return;
    const c = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, fontSize: 12 },
      rightPriceScale: { borderVisible: false },
      // отступ справа: подписи маркеров на последней свече не должны обрезаться краем графика
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false, rightOffset: 8 },
      crosshair: { mode: 0 },
    });
    const candle = c.addSeries(CandlestickSeries, { priceLineVisible: true });
    const volume = c.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" } });
    c.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    const sma20 = c.addSeries(LineSeries, { lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    const sma50 = c.addSeries(LineSeries, { lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    chart.current = c;
    series.current = { candle, volume, sma20, sma50, markers: createSeriesMarkers(candle, []) };
    return () => {
      c.remove();
      chart.current = null;
      series.current = null;
      lastKey.current = "";
    };
  }, []);

  // цвета следуют теме
  useEffect(() => {
    const c = chart.current;
    const s = series.current;
    if (!c || !s) return;
    const p = palette();
    c.applyOptions({
      layout: { textColor: p.text },
      grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
      timeScale: { borderColor: p.grid },
    });
    s.candle.applyOptions({
      upColor: p.up,
      downColor: p.down,
      wickUpColor: p.up,
      wickDownColor: p.down,
      borderVisible: false,
    });
    s.sma20.applyOptions({ color: p.accent });
    s.sma50.applyOptions({ color: p.warn });
  }, [theme]);

  // формат оси времени: локальное время пользователя (по умолчанию график показывает UTC)
  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    const daily = tf === "1d";
    c.applyOptions({
      timeScale: {
        timeVisible: !daily,
        // tickMarkType: 0 год, 1 месяц, 2 день месяца, 3+ время. Границы суток подписываем датой,
        // иначе на 4-часовом графике все метки одинаковые («03:00»)
        tickMarkFormatter: (time: Time, tickMarkType: number) => {
          const d = new Date((time as number) * 1000);
          if (tickMarkType === 0) return String(d.getFullYear());
          if (daily || tickMarkType < 3)
            return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
          return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
        },
      },
      localization: {
        timeFormatter: (time: Time) =>
          new Date((time as number) * 1000).toLocaleString("ru-RU", {
            day: "2-digit",
            month: "2-digit",
            year: "2-digit",
            ...(daily ? {} : { hour: "2-digit", minute: "2-digit" }),
          }),
      },
    });
  }, [tf]);

  // данные, оверлеи, маркеры
  useEffect(() => {
    const c = chart.current;
    const s = series.current;
    if (!c || !s) return;
    const p = palette();
    const closes = candles.map((x) => x.c);

    s.candle.setData(candles.map((x) => ({ time: ts(x.t), open: x.o, high: x.h, low: x.l, close: x.c })));
    s.volume.setData(
      candles.map((x) => ({
        time: ts(x.t),
        value: x.v,
        color: (x.c >= x.o ? p.up : p.down) + "66",
      })),
    );
    const line = (n: number) =>
      sma(closes, n).flatMap((v, i) => (v == null ? [] : [{ time: ts(candles[i].t), value: v }]));
    s.sma20.setData(line(20));
    s.sma50.setData(line(50));
    s.sma20.applyOptions({ visible: overlays.sma20 });
    s.sma50.applyOptions({ visible: overlays.sma50 });
    s.volume.applyOptions({ visible: overlays.volume });

    const times = candles.map((x) => x.t);
    const tfMs = TF_MS[tf] ?? 86_400_000;
    const marks: SeriesMarker<Time>[] = [];
    for (const g of signals) {
      if (g.tf !== tf) continue; // сигнал другого таймфрейма на этом графике не к месту
      const t = snapToCandle(times, g.candle_ts, tfMs);
      if (t == null) continue;
      marks.push({
        time: ts(t),
        position: g.side === "buy" ? "belowBar" : "aboveBar",
        shape: g.side === "buy" ? "arrowUp" : "arrowDown",
        color: g.side === "buy" ? p.up : p.down,
        text: g.side === "buy" ? "Вход" : "Выход",
      });
    }
    for (const e of trades) {
      const t = snapToCandle(times, e.ts, tfMs);
      if (t == null) continue;
      marks.push({
        time: ts(t),
        position: e.side === "buy" ? "belowBar" : "aboveBar",
        shape: "circle",
        color: p.accent,
        text: e.side === "buy" ? "Моя покупка" : "Моя продажа",
      });
    }
    marks.sort((a, b) => (a.time as number) - (b.time as number));
    s.markers.setMarkers(marks);

    if (lastKey.current !== resetKey && candles.length > 0) {
      c.timeScale().fitContent();
      lastKey.current = resetKey;
    }
  }, [candles, tf, signals, trades, overlays, resetKey, theme]);

  return <div ref={box} className="chart-box" role="img" aria-label="График цены" />;
}
