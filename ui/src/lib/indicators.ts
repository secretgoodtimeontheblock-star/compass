/** Простая скользящая средняя для оверлея на графике. Прогрев (n-1 первых точек) пропускается. */
export function sma(values: number[], n: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= n) sum -= values[i - n];
    if (i >= n - 1) out[i] = sum / n;
  }
  return out;
}

/** Время свечи (мс), в которую попадает момент ts; null — раньше первой свечи или позже последней. */
export function snapToCandle(times: number[], ts: number, tfMs: number): number | null {
  if (times.length === 0 || ts < times[0]) return null;
  let lo = 0;
  let hi = times.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (times[mid] <= ts) lo = mid;
    else hi = mid - 1;
  }
  return ts < times[lo] + tfMs ? times[lo] : null;
}

export const TF_MS: Record<string, number> = {
  "1m": 60_000,
  "5m": 300_000,
  "10m": 600_000,
  "15m": 900_000,
  "1h": 3_600_000,
  "4h": 14_400_000,
  "1d": 86_400_000,
};
