import type { Instrument, MarketId } from "../types";

// Подсказки для новичка: ликвидные тикеры, с которых удобно начать знакомство.
const QUICK: Record<MarketId, Instrument[]> = {
  moex: [
    { market: "moex", symbol: "SBER", name: "Сбербанк" },
    { market: "moex", symbol: "GAZP", name: "Газпром" },
    { market: "moex", symbol: "LKOH", name: "Лукойл" },
    { market: "moex", symbol: "YDEX", name: "Яндекс" },
  ],
  crypto: [
    { market: "crypto", symbol: "BTC/USDT", name: "BTC/USDT" },
    { market: "crypto", symbol: "ETH/USDT", name: "ETH/USDT" },
    { market: "crypto", symbol: "SOL/USDT", name: "SOL/USDT" },
  ],
};

interface Props {
  market: MarketId;
  items: Instrument[];
  selected: string | undefined;
  onSelect: (i: Instrument) => void;
  onRemove: (i: Instrument) => void;
  onQuickAdd: (i: Instrument) => void;
}

export function Watchlist({ market, items, selected, onSelect, onRemove, onQuickAdd }: Props) {
  const have = new Set(items.map((i) => i.symbol));
  const quick = QUICK[market].filter((q) => !have.has(q.symbol));

  return (
    <>
      <div className="panel-head">Избранное</div>
      <div className="scroll">
        {items.length === 0 && (
          <div className="empty">
            <b>Пока пусто.</b> Найдите тикер через поиск сверху или добавьте один из популярных ниже.
          </div>
        )}
        {items.map((i) => (
          <div
            key={i.symbol}
            className={`watch-item${i.symbol === selected ? " active" : ""}`}
            onClick={() => onSelect(i)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSelect(i);
              }
            }}
          >
            <div className="info">
              <span className="sym">{i.symbol}</span>
              {i.name !== i.symbol && <span className="nm">{i.name}</span>}
            </div>
            <button
              className="btn ghost small"
              aria-label={`Убрать ${i.symbol} из избранного`}
              onClick={(e) => {
                e.stopPropagation();
                onRemove(i);
              }}
            >
              ✕
            </button>
          </div>
        ))}
        {quick.length > 0 && (
          <>
            <div className="panel-head muted" style={{ fontWeight: 500 }}>
              Популярные
            </div>
            <div className="quick">
              {quick.map((q) => (
                <button key={q.symbol} className="btn small" onClick={() => onQuickAdd(q)}>
                  + {q.symbol}
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    </>
  );
}
