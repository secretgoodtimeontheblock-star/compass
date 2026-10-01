// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import * as Tabs from "@radix-ui/react-tabs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useApi, type ApiState } from "./use-api";
import { useScopedState } from "./use-scoped-state";
import { PersistentTab } from "../components/PersistentTab";
import { BacktestPanel } from "../components/BacktestPanel";
import { api } from "../api";
import type { Strategy } from "../types";

vi.mock("../components/EquityChart", () => ({ EquityChart: () => null }));
vi.mock("../components/OosPanel", () => ({ OosPanel: () => null }));
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
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

describe("данные выбранного инструмента", () => {
  it("не показывает старую цену или ошибку после смены тикера", async () => {
    let state!: ApiState<number>;
    const old = deferred<number>();
    const next = deferred<number>();
    function Probe({ symbol }: { symbol: string }) {
      state = useApi(() => symbol === "A" ? old.promise : next.promise, [symbol]);
      return null;
    }
    await act(() => root.render(<Probe symbol="A" />));
    await act(() => old.reject(new Error("A unavailable")));
    expect(state.error).toBe("A unavailable");
    await act(() => root.render(<Probe symbol="B" />));
    expect(state.error).toBeUndefined();
    expect(state.data).toBeUndefined();
    await act(() => next.resolve(200));
    expect(state.data).toBe(200);
  });

  it("отбрасывает запоздавший ответ предыдущего инструмента", async () => {
    let state!: ApiState<number>;
    const old = deferred<number>();
    const next = deferred<number>();
    function Probe({ symbol }: { symbol: string }) {
      state = useApi(() => symbol === "A" ? old.promise : next.promise, [symbol]);
      return null;
    }
    await act(() => root.render(<Probe symbol="A" />));
    await act(() => root.render(<Probe symbol="B" />));
    await act(() => next.resolve(200));
    await act(() => old.resolve(10));
    expect(state.data).toBe(200);
  });

  it("сохраняет независимый ввод по тикеру и режиму, включая отложенный ответ", async () => {
    let value = "";
    let change!: (s: string) => void;
    function Probe({ scope }: { scope: string }) {
      [value, change] = useScopedState(scope, "");
      return null;
    }
    await act(() => root.render(<Probe scope="BTC:paper" />));
    await act(() => change("80000"));
    const oldChange = change;
    await act(() => root.render(<Probe scope="ETH:paper" />));
    expect(value).toBe("");
    await act(() => change("3000"));
    await act(() => oldChange("81000"));
    expect(value).toBe("3000");
    await act(() => root.render(<Probe scope="BTC:real" />));
    expect(value).toBe("");
    await act(() => root.render(<Probe scope="BTC:paper" />));
    expect(value).toBe("81000");
  });
});

it("смена вкладки сохраняет черновик и скрывает неактивную форму", async () => {
  let edit!: (s: string) => void;
  function Form() {
    const [text, setText] = useState("");
    edit = setText;
    return <output>{text}</output>;
  }
  function Panel({ active }: { active: string }) {
    return <Tabs.Root value={active}><PersistentTab value="journal" active={active}><Form /></PersistentTab><PersistentTab value="other" active={active}>Other</PersistentTab></Tabs.Root>;
  }
  await act(() => root.render(<Panel active="journal" />));
  await act(() => edit("Мой план"));
  await act(() => root.render(<Panel active="other" />));
  expect(host.querySelector("output")?.closest("[hidden]")).not.toBeNull();
  await act(() => root.render(<Panel active="journal" />));
  expect(host.querySelector("output")?.textContent).toBe("Мой план");
  expect(host.querySelector("output")?.closest("[hidden]")).toBeNull();
});

it("бэктест передаёт закрытие в конце дня и не принимает ответ старой стратегии", async () => {
  const pending = deferred<never>();
  const spy = vi.spyOn(api, "backtest").mockReturnValue(pending.promise);
  const strategies: Strategy[] = [
    { id: "one", name: "One", description: "", params: [] },
    { id: "two", name: "Two", description: "", params: [] },
  ];
  await act(() => root.render(<BacktestPanel instrument={{ market: "crypto", symbol: "BTC/USDT", name: "BTC" }} tf="5m" strategies={strategies} settings={undefined} theme="dark" onStrategiesSaved={() => {}} />));
  const eod = [...host.querySelectorAll("label")].find((l) => l.textContent?.includes("Закрывать позицию к концу дня"))!.querySelector("input")!;
  await act(() => eod.click());
  const run = [...host.querySelectorAll("button")].find((b) => b.textContent?.includes("Проверить на BTC"))!;
  await act(() => run.click());
  expect(spy.mock.calls[0][0].close_eod).toBe(true);
  const select = host.querySelector("select")!;
  await act(() => { select.value = "two"; select.dispatchEvent(new Event("change", { bubbles: true })); });
  await act(() => pending.reject(new Error("old result")));
  expect(host.textContent).not.toContain("old result");
});
