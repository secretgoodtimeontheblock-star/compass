import { AreaSeries, ColorType, createChart, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";

interface Props {
  points: { t: number; v: number }[];
  theme: string;
}

/** Кривая капитала бэктеста: как менялся счёт, если бы торговали по правилу стратегии. */
export function EquityChart({ points, theme }: Props) {
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!box.current || points.length === 0) return;
    const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
    const chart = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: css("--muted"), fontSize: 11 },
      grid: { vertLines: { visible: false }, horzLines: { color: css("--border") } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
      handleScroll: false,
      handleScale: false,
    });
    const area = chart.addSeries(AreaSeries, {
      lineColor: css("--accent"),
      topColor: css("--accent") + "55",
      bottomColor: css("--accent") + "00",
      lineWidth: 2,
      priceLineVisible: false,
    });
    area.setData(points.map((p) => ({ time: (p.t / 1000) as UTCTimestamp, value: p.v })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [points, theme]);

  return <div ref={box} className="equity" role="img" aria-label="Кривая капитала" />;
}
