// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { Presets } from "../components/Presets";
import { TickerIcon } from "../components/TickerIcon";
import { baseSymbol, tickerStyle } from "./ticker-style";
import type { PresetGroup } from "../types";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(() => root.unmount());
  host.remove();
  vi.restoreAllMocks();
});

describe("иконки тикеров", () => {
  it("узнаваемы у монет и стабильны у остальных", () => {
    expect(baseSymbol("BTC/USDT")).toBe("BTC");
    expect(tickerStyle("BTC/USDT", "crypto")).toMatchObject({ text: "₿", bg: "#f7931a", round: true });
    expect(tickerStyle("ETH/USDT", "crypto").text).toBe("Ξ");
    const a = tickerStyle("SBER", "moex");
    expect(a).toEqual(tickerStyle("SBER", "moex")); // один тикер — всегда один вид
    expect(a.text).toBe("SB");
    expect(tickerStyle("GAZP", "moex").bg).not.toBe(a.bg);
    expect(tickerStyle("T", "moex").text).toBe("T");
  });

  it("фонды и облигации — скруглённый квадрат, акции — круг; знак читается на любом фоне", () => {
    expect(tickerStyle("TMOS", "moex", "share", "TMOS ETF").round).toBe(false);
    expect(tickerStyle("SU26238", "moex", "bond").round).toBe(false);
    expect(tickerStyle("SBER", "moex", "share", "Сбербанк").round).toBe(true);
    expect(tickerStyle("BNB/USDT", "crypto").fg).toBe("#14161a"); // светло-жёлтый фон — тёмный знак
    expect(tickerStyle("XRP/USDT", "crypto").fg).toBe("#fff"); // тёмный фон — светлый знак
  });

  it("рисуется скрытой от скринридера", async () => {
    await act(() => root.render(<TickerIcon symbol="SBER" market="moex" />));
    expect(host.querySelector('[data-testid="ticker-icon"]')?.getAttribute("aria-hidden")).toBe("true");
  });
});

const groups: PresetGroup[] = [
  {
    id: "banks", title: "Банки", icon: "bank", note: "Банки и биржа.",
    instruments: [
      { market: "moex", symbol: "SBER", name: "Сбербанк", kind: "share", watched: true },
      { market: "moex", symbol: "VTBR", name: "ВТБ", kind: "share", watched: false },
      { market: "moex", symbol: "MOEX", name: "МосБиржа", kind: "share", watched: false },
    ],
  },
  { id: "x", title: "Неизвестная иконка", icon: "no-such", note: "", instruments: [{ market: "moex", symbol: "AAA", name: "A", watched: false }] },
];
const button = (text: string) => [...host.querySelectorAll("button")].find((b) => b.textContent?.includes(text));

describe("наборы по темам", () => {
  it("раскрывает набор, блокирует уже добавленное и добавляет остальное одной кнопкой", async () => {
    vi.spyOn(api, "presets").mockResolvedValue(groups);
    const add = vi.spyOn(api, "addPreset").mockResolvedValue({ added: 2, already: 1, title: "Банки" });
    const onChanged = vi.fn();
    await act(async () => root.render(<Presets market="moex" onQuickAdd={() => {}} onChanged={onChanged} />));
    expect(host.textContent).toContain("Банки");
    expect(host.textContent).not.toContain("VTBR"); // свёрнуто
    await act(() => button("Банки")!.click());
    const sber = button("SBER") as HTMLButtonElement;
    expect(sber.disabled).toBe(true);
    expect((button("VTBR") as HTMLButtonElement).disabled).toBe(false);
    await act(() => button("Добавить все (2)")!.click());
    expect(add).toHaveBeenCalledWith("moex", "banks");
    expect(host.querySelector('[role="status"]')?.textContent).toContain("добавлено 2, уже были 1");
    expect(onChanged).toHaveBeenCalled();
  });

  it("добавляет один тикер из набора и не падает на неизвестной иконке группы", async () => {
    vi.spyOn(api, "presets").mockResolvedValue(groups);
    const quick = vi.fn();
    await act(async () => root.render(<Presets market="moex" onQuickAdd={quick} />));
    await act(() => button("Неизвестная иконка")!.click());
    await act(() => button("AAA")!.click());
    expect(quick).toHaveBeenCalledWith(expect.objectContaining({ symbol: "AAA" }));
  });

  it("сообщает об ошибке загрузки, а не молчит", async () => {
    vi.spyOn(api, "presets").mockRejectedValue(new Error("нет связи"));
    await act(async () => root.render(<Presets market="crypto" onQuickAdd={() => {}} />));
    expect(host.textContent).toContain("Наборы недоступны: нет связи");
  });
});
