// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { CockpitPanel } from "../components/CockpitPanel";
import { LabPanel } from "../components/LabPanel";
import { Learn } from "../components/Learn";
import { ExperienceContext, atLeast } from "./experience";
import { parseGrid, suggestGrid } from "./grid";
import type { CockpitResponse, ExperienceLevel, LabResponse, Strategy } from "../types";

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

const strat: Strategy = {
  id: "sma_cross",
  name: "SMA",
  description: "",
  params: [
    { name: "fast", label: "Быстрая", default: 20, min: 2, max: 200 },
    { name: "slow", label: "Медленная", default: 50, min: 3, max: 400 },
  ],
};
const request = { market: "moex", symbol: "SBER", tf: "1d", strategy: "sma_cross", params: {}, limit: 1000, capital: 100000, fee_pct: 0.05, slippage_pct: 0.05 } as const;

const lab: LabResponse = {
  verdict: {
    status: "fragile",
    headline: "Результат ненадёжен: малые изменения параметров разрушают результат",
    findings: [{ code: "params_fragile", severity: "bad", title: "Малые изменения параметров разрушают результат", text: "Пик одиночный.", learn: ["overfitting"] }],
    disclaimer: "Это описание прошлого.",
  },
  walk_forward: {
    summary: { mode: "rolling", optimized: true, folds: 2, usable_folds: 2, profitable_folds: 1, stitched_return_pct: 3.2, mean_return_pct: 1.6, worst_fold_pct: -2, trades: 12, param_consistency: 0.5, efficiency: null },
    folds: [
      { index: 0, train_from: 0, train_to: 1, test_from: 86_400_000, test_to: 172_800_000, params: { fast: 5, slow: 20 }, train_score: 1, train_return_pct: 10, test_return_pct: 5, test_trades: 6, test_max_drawdown_pct: -1, buy_hold_pct: 2, status: "ok" },
      { index: 1, train_from: 0, train_to: 1, test_from: 259_200_000, test_to: 345_600_000, params: null, train_score: null, train_return_pct: null, test_return_pct: -2, test_trades: 6, test_max_drawdown_pct: -3, buy_hold_pct: 1, status: "no_choice" },
    ],
  },
  parameter_map: { available: true, params: ["fast", "slow"], axes: [[2, 5], [10, 20]], matrix: [[{ valid: true, return_pct: 12, trades: 11 }, { valid: true, return_pct: -4, trades: 12 }], [{ valid: false }, { valid: true, return_pct: 3, trades: 9, thin: true }]], stability: { verdict: "fragile", text: "Соседи хуже." } },
  robustness: { bootstrap: null, concentration: null, drawdown: null, tail: null, regimes: null, cost_stress: [{ multiplier: 1, fee_pct: 0.05, slippage_pct: 0.05, spread_pct: 0.05, return_pct: 3.2, trades: 12 }] },
  multiple_testing: { variants_this_run: 4, prior_variants: 0, pbo: { available: true, value: 0.62 }, dsr: { available: false, value: null } },
  research: null,
  notes: ["Прошлое не гарантирует будущее."],
  warnings: [],
  trials: { prior_variants: 0, this_run_variants: 4, total_variants: 4, warning: null },
};

const click = async (el: Element | undefined | null) => {
  expect(el).toBeTruthy();
  await act(() => (el as HTMLElement).click());
};
const button = (text: string) => [...host.querySelectorAll("button")].find((b) => b.textContent?.includes(text));

function renderAt(level: ExperienceLevel, node: React.ReactNode) {
  return act(() => root.render(<ExperienceContext.Provider value={level}>{node}</ExperienceContext.Provider>));
}

describe("уровни интерфейса", () => {
  it("упорядочены: исследователь видит всё, что видит торгующий и начинающий", () => {
    expect(atLeast("researcher", "trader") && atLeast("trader", "beginner") && atLeast("researcher", "beginner")).toBe(true);
    expect(atLeast("beginner", "trader") || atLeast("trader", "researcher")).toBe(false);
  });
});

describe("лаборатория проверки", () => {
  it("новичку даёт одну кнопку и вывод простым языком без слоя исследователя", async () => {
    const spy = vi.spyOn(api, "lab").mockResolvedValue(lab);
    await renderAt("beginner", <LabPanel strategy={strat} request={{ ...request }} />);
    expect(host.textContent).not.toContain("Окон проверки"); // нет ни сетки, ни числа окон
    await click(button("Проверить надёжность"));
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0][0]).toMatchObject({ grid: {}, folds: 4, mode: "rolling" });
    expect(host.textContent).toContain("Результат ненадёжен");
    expect(host.textContent).toContain("Пик одиночный.");
    expect(host.textContent).not.toContain("Слой исследователя");
    expect(host.querySelector("table")).toBeNull();
  });

  it("исследователю показывает окна, карту параметров и поправки на множественные проверки", async () => {
    const spy = vi.spyOn(api, "lab").mockResolvedValue(lab);
    await renderAt("researcher", <LabPanel strategy={strat} request={{ ...request }} />);
    await click([...host.querySelectorAll("label")].find((l) => l.textContent?.includes("Подбирать параметры"))?.querySelector("input"));
    await click(button("Запустить лабораторию"));
    expect(spy.mock.calls[0][0].grid).toEqual({ fast: [10, 20, 30, 40], slow: [25, 50, 75, 100] });
    expect(host.querySelector('table[aria-label="Окна walk-forward"]')?.textContent).toContain("нет выбора");
    const map = host.querySelector('table[aria-label="Карта параметров"]');
    expect(map?.textContent).toContain("12");
    expect(map?.textContent).toContain("—"); // недопустимое сочетание
    expect(host.textContent).toContain("PBO");
    expect(host.textContent).toContain("62%");
  });

  it("не показывает результат прошлых настроек после их смены", async () => {
    vi.spyOn(api, "lab").mockResolvedValue(lab);
    await renderAt("beginner", <LabPanel strategy={strat} request={{ ...request }} />);
    await click(button("Проверить надёжность"));
    expect(host.textContent).toContain("ненадёжен");
    await renderAt("beginner", <LabPanel strategy={strat} request={{ ...request, capital: 5 }} />);
    expect(host.textContent).not.toContain("ненадёжен");
  });

  it("показывает ошибку расчёта человеческим текстом", async () => {
    vi.spyOn(api, "lab").mockRejectedValue(new Error("Мало истории для 4 окон"));
    await renderAt("beginner", <LabPanel strategy={strat} request={{ ...request }} />);
    await click(button("Проверить надёжность"));
    expect(host.querySelector(".error")?.textContent).toContain("Мало истории");
  });
});

describe("сетка перебора", () => {
  it("предлагает значения в границах и понятно отвергает не целые", () => {
    const g = suggestGrid(strat);
    expect(g.fast).toBe("10, 20, 30, 40");
    expect(parseGrid(strat, g, ["fast"])).toEqual({ fast: [10, 20, 30, 40] });
    expect(() => parseGrid(strat, { fast: "1.5" }, ["fast"])).toThrow("Быстрая");
    expect(() => parseGrid(strat, {}, ["slow"])).toThrow("Медленная");
  });
});

const cockpit: CockpitResponse = {
  generated_at: 0,
  status: "stop",
  headline: "Дневной лимит убытка достигнут: по вашему собственному плану на сегодня стоп.",
  questions: [{ id: "rules", question: "Нарушаю ли я собственные правила?", answer: "Нарушено правил: 1.", status: "bad", items: [] }],
  attention: [
    { severity: "stop", section: "rules", text: "Рубли: дневной лимит убытка достигнут.", hint: "", code: "daily_loss", learn: ["daily_loss_limit"] },
    { severity: "bad", section: "rules", text: "Рубли: у GAZP не записан стоп.", hint: "Запишите стоп.", code: "no_stop", learn: ["stop_loss"] },
    { severity: "attention", section: "signals", text: "Новых сигналов: 2.", hint: "", code: "", learn: [] },
    { severity: "info", section: "now", text: "Тихие часы.", hint: "", code: "", learn: [] },
  ],
  notes: ["Кокпит ничего не блокирует."],
};

describe("кокпит решений", () => {
  it("новичку показывает статус и главное, без списка из семи вопросов", async () => {
    vi.spyOn(api, "cockpit").mockResolvedValue(cockpit);
    await renderAt("beginner", <CockpitPanel />);
    expect(host.querySelector('[role="status"]')?.textContent).toContain("Стоп на сегодня");
    expect(host.textContent).toContain("у GAZP не записан стоп");
    expect(button("Все вопросы дня")).toBeUndefined();
  });

  it("торгующему даёт развернуть все вопросы дня", async () => {
    vi.spyOn(api, "cockpit").mockResolvedValue(cockpit);
    await renderAt("trader", <CockpitPanel />);
    expect(host.textContent).not.toContain("Нарушаю ли я собственные правила?");
    await click(button("Все вопросы дня"));
    expect(host.textContent).toContain("Нарушаю ли я собственные правила?");
    expect(host.textContent).toContain("Кокпит ничего не блокирует.");
  });

  it("сообщает, что кокпит недоступен, а не молчит", async () => {
    vi.spyOn(api, "cockpit").mockRejectedValue(new Error("нет связи"));
    await renderAt("trader", <CockpitPanel />);
    expect(host.textContent).toContain("Кокпит недоступен: нет связи");
  });
});

describe("«что это?» рядом с находкой", () => {
  it("открывает определение понятия из глоссария", async () => {
    vi.spyOn(api, "glossary").mockResolvedValue([
      { id: "slippage", term: "Проскальзывание", short: "Разница между ожидаемой и фактической ценой.", why: "Съедает прибыль частых сделок." },
    ]);
    await renderAt("trader", <Learn ids={["slippage"]} />);
    await click(host.querySelector("button.chip"));
    await act(async () => { await Promise.resolve(); });
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog?.textContent).toContain("Проскальзывание");
    expect(dialog?.textContent).toContain("Съедает прибыль");
  });

  it("ничего не рисует без понятий", async () => {
    await renderAt("trader", <Learn ids={[]} />);
    expect(host.querySelector("button")).toBeNull();
  });
});
