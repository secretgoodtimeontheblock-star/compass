import { useCallback, useRef, useState, type SetStateAction } from "react";

/** Независимые черновики для каждого инструмента и режима журнала. */
export function useScopedState<T>(key: string, initial: T | (() => T)) {
  const [values, setValues] = useState<Record<string, T>>({});
  const defaults = useRef(new Map<string, T>());
  if (!defaults.current.has(key)) defaults.current.set(key, typeof initial === "function" ? (initial as () => T)() : initial);
  const value = Object.hasOwn(values, key) ? values[key] : defaults.current.get(key)!;
  const setValue = useCallback((next: SetStateAction<T>) => {
    setValues((previous) => {
      const current = Object.hasOwn(previous, key) ? previous[key] : defaults.current.get(key)!;
      return { ...previous, [key]: typeof next === "function" ? (next as (old: T) => T)(current) : next };
    });
  }, [key]);
  return [value, setValue] as const;
}
