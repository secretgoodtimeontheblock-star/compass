// Внешний вид иконки тикера. Логотипов из сети нет (приложение работает без лишних запросов),
// поэтому иконка рисуется локально: цвет и знак монеты узнаваемы, у остальных цвет получается из тикера —
// один и тот же тикер всегда выглядит одинаково.

const COIN: Record<string, { bg: string; glyph?: string }> = {
  BTC: { bg: "#f7931a", glyph: "₿" },
  ETH: { bg: "#627eea", glyph: "Ξ" },
  SOL: { bg: "#7c4dff" },
  BNB: { bg: "#f3ba2f" },
  XRP: { bg: "#23292f" },
  DOGE: { bg: "#c2a633", glyph: "Ð" },
  ADA: { bg: "#0033ad" },
  TRX: { bg: "#ef0027" },
  AVAX: { bg: "#e84142" },
  LINK: { bg: "#2a5ada" },
  DOT: { bg: "#e6007a" },
  LTC: { bg: "#345d9d", glyph: "Ł" },
  BCH: { bg: "#0ac18e" },
  SHIB: { bg: "#e4510d" },
  NEAR: { bg: "#111111" },
  UNI: { bg: "#ff007a" },
  ATOM: { bg: "#2e3148" },
  USDT: { bg: "#26a17b", glyph: "₮" },
  USDC: { bg: "#2775ca" },
};

export interface TickerStyle {
  text: string;
  bg: string;
  fg: string;
  round: boolean; // монеты и акции — круг; фонды и облигации — скруглённый квадрат
}

/** «BTC/USDT» → «BTC»; для акций тикер как есть. */
export function baseSymbol(symbol: string): string {
  return symbol.split("/")[0].toUpperCase();
}

function hash(text: string): number {
  let h = 0;
  for (const ch of text) h = (h * 31 + ch.codePointAt(0)!) >>> 0;
  return h;
}

/** Светлый фон — тёмный знак, тёмный — светлый (по воспринимаемой яркости). */
function readable(bg: string): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(bg);
  if (!m) return "#fff";
  const n = parseInt(m[1], 16);
  const lum = 0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255);
  return lum > 150 ? "#14161a" : "#fff";
}

function hsl(h: number, s: number, l: number): string {
  const k = (n: number) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) => Math.round(255 * (l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)))));
  return `#${[f(0), f(8), f(4)].map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

export function tickerStyle(symbol: string, market: string, kind?: string, name?: string): TickerStyle {
  const base = baseSymbol(symbol);
  const coin = market === "crypto" ? COIN[base] : undefined;
  const bg = coin?.bg ?? hsl(hash(base) % 360, 0.55, 0.42);
  const etf = market === "moex" && (kind === "bond" || /ETF|БПИФ/i.test(name ?? ""));
  return {
    text: coin?.glyph ?? base.slice(0, base.length <= 3 ? base.length : 2),
    bg,
    fg: readable(bg),
    round: !etf && kind !== "bond",
  };
}
