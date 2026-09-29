import { describe, expect, it } from "vitest";
import { fmtPct, fmtPrice, pnlClass, priceDigits, toLocalInput } from "./format";
import { snapToCandle, sma } from "./indicators";

describe("sma", () => {
  it("пропускает прогрев и считает среднее окна", () => {
    expect(sma([1, 2, 3, 4, 5], 3)).toEqual([null, null, 2, 3, 4]);
  });
  it("окно длиннее ряда — везде null", () => {
    expect(sma([1, 2], 5)).toEqual([null, null]);
  });
});

describe("snapToCandle", () => {
  const times = [1000, 2000, 3000];
  it("момент внутри свечи → время её начала", () => {
    expect(snapToCandle(times, 2500, 1000)).toBe(2000);
    expect(snapToCandle(times, 2000, 1000)).toBe(2000);
  });
  it("раньше первой свечи → null", () => {
    expect(snapToCandle(times, 999, 1000)).toBeNull();
  });
  it("позже конца последней свечи → null, а не маркер на чужой свече", () => {
    expect(snapToCandle(times, 4000, 1000)).toBeNull();
    expect(snapToCandle(times, 3999, 1000)).toBe(3000);
  });
  it("пустой ряд → null", () => {
    expect(snapToCandle([], 1, 1000)).toBeNull();
  });
  it("дыра между свечами (выходные) → null", () => {
    expect(snapToCandle([1000, 5000], 3000, 1000)).toBeNull();
  });
});

describe("format", () => {
  it("знаков после запятой тем больше, чем дешевле актив", () => {
    expect(priceDigits(84000)).toBe(2);
    expect(priceDigits(5)).toBe(4);
    expect(priceDigits(0.5)).toBe(5);
    expect(priceDigits(0.00001)).toBe(8);
  });
  it("прочерк вместо NaN/null", () => {
    expect(fmtPrice(null)).toBe("—");
    expect(fmtPrice(Number.NaN)).toBe("—");
    expect(fmtPct(undefined)).toBe("—");
  });
  it("плюс у положительных процентов, без знака по флагу", () => {
    expect(fmtPct(1.5)).toContain("+");
    expect(fmtPct(1.5, 2, false)).not.toContain("+");
    expect(fmtPct(-1.5)).toContain("-");
  });
  it("класс прибыли/убытка", () => {
    expect(pnlClass(1)).toBe("up");
    expect(pnlClass(-1)).toBe("down");
    expect(pnlClass(0)).toBe("");
    expect(pnlClass(null)).toBe("");
  });
  it("datetime-local в локальном времени и обратно без сдвига", () => {
    const ms = new Date(2026, 8, 29, 17, 4).getTime();
    expect(toLocalInput(ms)).toBe("2026-09-29T17:04");
    expect(new Date(toLocalInput(ms)).getTime()).toBe(ms);
  });
});
