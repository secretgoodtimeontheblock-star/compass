import { createContext, useCallback, useContext, useState } from "react";
import { ApiError } from "../api";
import type { AiResult } from "../types";

/** Что приложение даёт любому месту, где вызывается AI: получить согласие и открыть настройки. */
export interface AiContextValue {
  requestConsent: () => Promise<boolean>;
  openSettings: () => void;
}

export const AiContext = createContext<AiContextValue>({
  requestConsent: async () => false,
  openSettings: () => undefined,
});

export interface AiRun {
  result: AiResult | undefined;
  error: string | undefined;
  errorCode: string | undefined;
  busy: boolean;
  run: (call: (refresh: boolean) => Promise<AiResult>, refresh?: boolean) => Promise<void>;
  reset: () => void;
}

/**
 * Запуск AI-запроса с общей обработкой: нет согласия на отправку данных → спрашиваем и
 * повторяем один раз; остальные ошибки показываем текстом. Кнопки блокируются на время
 * запроса (Cursor CLI отвечает 15–30 с), чтобы двойной клик не запускал два процесса.
 */
export function useAiRun(): AiRun {
  const ctx = useContext(AiContext);
  const [result, setResult] = useState<AiResult>();
  const [error, setError] = useState<string>();
  const [errorCode, setErrorCode] = useState<string>();
  const [busy, setBusy] = useState(false);

  const run = useCallback(
    async (call: (refresh: boolean) => Promise<AiResult>, refresh = false) => {
      setBusy(true);
      setError(undefined);
      setErrorCode(undefined);
      try {
        try {
          setResult(await call(refresh));
        } catch (e) {
          if (e instanceof ApiError && e.code === "consent") {
            if (!(await ctx.requestConsent())) throw new ApiError("Без вашего согласия AI не используется.", 409, "declined");
            setResult(await call(refresh));
          } else {
            throw e;
          }
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "Ошибка AI");
        setErrorCode(e instanceof ApiError ? e.code : undefined);
      } finally {
        setBusy(false);
      }
    },
    [ctx],
  );

  const reset = useCallback(() => {
    setResult(undefined);
    setError(undefined);
    setErrorCode(undefined);
  }, []);

  return { result, error, errorCode, busy, run, reset };
}
