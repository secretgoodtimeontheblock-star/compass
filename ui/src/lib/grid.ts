import type { Strategy } from "../types";

/** Значения по умолчанию для перебора: от половины до двух значений параметра в допустимых границах. */
export function suggestGrid(strategy: Strategy): Record<string, string> {
  const out: Record<string, string> = {};
  for (const p of strategy.params) {
    const raw = [0.5, 1, 1.5, 2].map((k) => Math.round(p.default * k));
    const vals = [...new Set(raw.map((v) => Math.min(p.max, Math.max(p.min, v))))];
    out[p.name] = vals.join(", ");
  }
  return out;
}

/** Текст «5, 10, 20» → числа; бросает понятную ошибку, если что-то не целое. */
export function parseGrid(strategy: Strategy, grid: Record<string, string>, names: string[]): Record<string, number[]> {
  const out: Record<string, number[]> = {};
  for (const name of names) {
    const label = strategy.params.find((p) => p.name === name)?.label ?? name;
    const nums = (grid[name] ?? "").split(/[,\s;]+/).filter(Boolean).map(Number);
    if (nums.length === 0 || nums.some((n) => !Number.isInteger(n))) {
      throw new Error(`Параметр «${label}»: перечислите целые числа через запятую`);
    }
    out[name] = nums;
  }
  return out;
}
