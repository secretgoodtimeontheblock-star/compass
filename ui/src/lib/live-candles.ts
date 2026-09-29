import type { Candle, CandlesResponse } from "../types";

export interface LiveUpdate { candle: Candle; receivedAt: number }

export function mergeLiveHistory(history: CandlesResponse | undefined, updates: LiveUpdate[]): Candle[] {
  const after = (history?.fetched_at ?? 0) * 1000;
  return mergeCandles(history?.candles ?? [], updates.filter((u) => u.receivedAt >= after).map((u) => u.candle));
}

/** Поток заменяет свечу целиком: объём уже накопительный, его нельзя суммировать. */
export function mergeCandles(history: Candle[], updates: Candle[], limit = 500): Candle[] {
  const rows = new Map(history.map((c) => [c.t, c]));
  for (const c of updates) rows.set(c.t, c);
  return [...rows.values()].sort((a, b) => a.t - b.t).slice(-limit);
}

/** Интервал при обрыве нельзя соединять выдуманными свечами. */
export function hasGap(candles: Candle[], tfMs: number): boolean {
  return candles.some((c, i) => i > 0 && c.t - candles[i - 1].t > tfMs);
}
