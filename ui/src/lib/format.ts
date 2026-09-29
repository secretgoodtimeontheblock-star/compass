// Форматирование чисел и дат. Локаль — русская; цены подбирают число знаков по величине,
// чтобы BTC (84 000) и мелкие альты (0,000012) читались одинаково хорошо.

export function priceDigits(p: number): number {
  const a = Math.abs(p);
  if (a >= 1000) return 2;
  if (a >= 100) return 2;
  if (a >= 1) return 4;
  if (a >= 0.01) return 5;
  return 8;
}

export function fmtPrice(p: number | null | undefined): string {
  if (p == null || Number.isNaN(p)) return "—";
  return p.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: priceDigits(p) });
}

export function fmtNum(n: number | null | undefined, digits = 2): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString("ru-RU", { minimumFractionDigits: 0, maximumFractionDigits: digits });
}

export function fmtPct(n: number | null | undefined, digits = 2, sign = true): string {
  if (n == null || Number.isNaN(n)) return "—";
  const s = n.toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return `${sign && n > 0 ? "+" : ""}${s}%`;
}

export function fmtDate(ms: number): string {
  return new Date(ms).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "2-digit" });
}

export function fmtDateTime(ms: number): string {
  return new Date(ms).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Значение для <input type="datetime-local"> в ЛОКАЛЬНОМ времени. */
export function toLocalInput(ms: number): string {
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Класс окраски прибыли/убытка. */
export function pnlClass(n: number | null | undefined): string {
  if (n == null || n === 0) return "";
  return n > 0 ? "up" : "down";
}

export const STRATEGY_SHORT: Record<string, string> = {
  sma_cross: "Пересечение средних",
  rsi_reversion: "RSI",
  donchian: "Пробой канала",
};
