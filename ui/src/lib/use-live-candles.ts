import { useEffect, useRef, useState } from "react";
import type { Candle, CandlesResponse, LiveState } from "../types";
import type { LiveUpdate } from "./live-candles";

interface Feed {
  key: string;
  state: LiveState;
  message: string;
  history?: CandlesResponse;
  updates: LiveUpdate[];
  receivedAt?: number;
}

export function useLiveCandles(symbol: string | undefined, tf: string, enabled: boolean) {
  const key = enabled && symbol ? `${symbol}:${tf}` : "";
  const [feed, setFeed] = useState<Feed>();
  const [attempt, setAttempt] = useState(0);
  const lastReceived = useRef(0);

  useEffect(() => {
    if (!key || !symbol) return;
    let active = true;
    lastReceived.current = 0;
    setFeed({ key, state: "connecting", message: "Подключаем поток OKX…", updates: [] });
    const connection = new EventSource(`/api/live?${new URLSearchParams({ symbol, tf })}`);
    const update = (fn: (f: Feed) => Feed) => {
      if (active) setFeed((f) => f?.key === key ? fn(f) : f);
    };
    connection.addEventListener("status", (event) => {
      const status = JSON.parse((event as MessageEvent).data) as { state: LiveState; message: string };
      update((f) => ({ ...f, ...status }));
      if (status.state === "unavailable") connection.close();
    });
    connection.addEventListener("history", (event) => {
      const history = JSON.parse((event as MessageEvent).data) as CandlesResponse;
      // Новый REST-снимок после обрыва приоритетнее старых промежуточных свечей.
      update((f) => ({ ...f, history, updates: [] }));
    });
    connection.addEventListener("history_error", () => {
      update((f) => ({ ...f, history: f.history ? { ...f.history, stale: true } : undefined }));
    });
    connection.addEventListener("candle", (event) => {
      const { candle } = JSON.parse((event as MessageEvent).data) as { candle: Candle };
      lastReceived.current = Date.now();
      update((f) => ({
        ...f, state: "live", message: "Поток OKX подключён",
        receivedAt: lastReceived.current,
        updates: [...f.updates.filter((u) => u.candle.t !== candle.t), { candle, receivedAt: lastReceived.current }].slice(-500),
      }));
    });
    connection.onerror = () => update((f) => ({
      ...f, state: "reconnecting", message: "Нет связи с потоком. Переподключаемся автоматически…",
    }));
    const timer = setInterval(() => {
      if (lastReceived.current && Date.now() - lastReceived.current > 15_000) {
        update((f) => f.state === "live" ? {
          ...f, state: "reconnecting", message: "Обновления задерживаются. Проверяем соединение…",
        } : f);
      }
    }, 1000);
    return () => {
      active = false;
      clearInterval(timer);
      connection.close();
    };
  }, [key, symbol, tf, attempt]);

  return { feed: feed?.key === key && key ? feed : undefined, retry: () => setAttempt((n) => n + 1) };
}
