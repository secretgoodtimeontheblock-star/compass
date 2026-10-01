import { tickerStyle } from "../lib/ticker-style";

interface Props {
  symbol: string;
  market: string;
  kind?: string;
  name?: string;
  size?: number;
}

/** Круглая иконка инструмента: помогает находить тикер взглядом. Для скринридера скрыта — тикер рядом текстом. */
export function TickerIcon({ symbol, market, kind, name, size = 22 }: Props) {
  const s = tickerStyle(symbol, market, kind, name);
  return (
    <span
      className="ticker-icon"
      aria-hidden="true"
      data-testid="ticker-icon"
      style={{
        width: size,
        height: size,
        background: s.bg,
        color: s.fg,
        borderRadius: s.round ? "50%" : Math.round(size * 0.28),
        fontSize: Math.round(size * (s.text.length > 2 ? 0.34 : 0.42)),
      }}
    >
      {s.text}
    </span>
  );
}
