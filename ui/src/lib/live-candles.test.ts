import { describe, expect, it } from "vitest";
import type { Candle, CandlesResponse } from "../types";
import { hasGap, mergeCandles, mergeLiveHistory } from "./live-candles";

const c = (t: number, price = 10): Candle => ({ t, o: 10, h: 15, l: 5, c: price, v: 7 });

describe("поток свечей", () => {
  it("заменяет незакрытую свечу без дублей и сложения объёма", () => {
    expect(mergeCandles([c(1), c(2)], [c(2, 12), c(3)])).toEqual([c(1), c(2, 12), c(3)]);
  });
  it("сортирует историю и ограничивает размер буфера", () => {
    expect(mergeCandles([c(3), c(1)], [c(2)], 2)).toEqual([c(2), c(3)]);
  });
  it("обнаруживает пропуск, не выдумывая свечу", () => {
    expect(hasGap([c(60_000), c(180_000)], 60_000)).toBe(true);
    expect(hasGap([c(60_000), c(120_000)], 60_000)).toBe(false);
  });
  it("старое событие не затирает восстановленный REST-снимок", () => {
    const history: CandlesResponse = { source: "crypto:okx", fetched_at: 100, stale: false, candles: [c(1, 13)] };
    expect(mergeLiveHistory(history, [{ candle: c(1, 11), receivedAt: 99_000 }])).toEqual([c(1, 13)]);
    expect(mergeLiveHistory(history, [{ candle: c(1, 14), receivedAt: 101_000 }])).toEqual([c(1, 14)]);
  });
});
