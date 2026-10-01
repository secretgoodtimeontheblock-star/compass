import { useCallback, useEffect, useRef, useState } from "react";

export interface ApiState<T> {
  data: T | undefined;
  error: string | undefined;
  loading: boolean;
  reload: () => void;
}

/**
 * Загрузка данных с защитой от гонки: ответ на устаревший запрос (пользователь уже
 * переключил тикер) отбрасывается. `pollMs` — тихое автообновление без индикатора загрузки.
 * `fn === null` — «пока нечего грузить».
 */
export function useApi<T>(fn: (() => Promise<T>) | null, deps: unknown[], pollMs?: number): ApiState<T> {
  const [data, setData] = useState<T>();
  const dataDeps = useRef<unknown[]>([]);
  const currentDeps = useRef(deps);
  currentDeps.current = deps;
  const [error, setError] = useState<string>();
  const errorDeps = useRef<unknown[]>([]);
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);
  const seq = useRef(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const run = useCallback(async (silent: boolean) => {
    const f = fnRef.current;
    if (!f) return;
    const id = ++seq.current;
    const requestedDeps = currentDeps.current;
    if (!silent) setLoading(true);
    try {
      const res = await f();
      if (id !== seq.current) return;
      dataDeps.current = requestedDeps;
      setData(res);
      setError(undefined);
    } catch (e) {
      if (id !== seq.current) return;
      errorDeps.current = requestedDeps;
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (id === seq.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!fnRef.current) {
      seq.current++;
      setData(undefined);
      setError(undefined);
      setLoading(false);
      return;
    }
    void run(false);
    return () => { seq.current++; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  useEffect(() => {
    if (!pollMs) return;
    const id = setInterval(() => void run(true), pollMs);
    return () => clearInterval(id);
  }, [pollMs, run]);

  const same = dataDeps.current.length === deps.length && deps.every((d, i) => Object.is(d, dataDeps.current[i]));
  const sameError = errorDeps.current.length === deps.length && deps.every((d, i) => Object.is(d, errorDeps.current[i]));
  return { data: fn && same ? data : undefined, error: fn && sameError ? error : undefined, loading, reload: () => setTick((t) => t + 1) };
}

/** localStorage без падений: в приватном окне/заблокированном хранилище просто без памяти. */
export function loadPref(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
export function savePref(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* без хранилища интерфейс работает, просто ничего не запоминает */
  }
}
