import type { Instrument, MarketId } from "../types";
import { Icon } from "./Icon";
import { Presets } from "./Presets";
import { TickerIcon } from "./TickerIcon";

// Подсказки для новичка: ликвидные тикеры, с которых удобно начать знакомство.
const QUICK: Record<MarketId, Instrument[]> = {
  moex: [
    { market: "moex", symbol: "SBER", name: "Сбербанк" },
    { market: "moex", symbol: "GAZP", name: "Газпром" },
    { market: "moex", symbol: "LKOH", name: "Лукойл" },
    { market: "moex", symbol: "YDEX", name: "Яндекс" },
    { market: "moex", symbol: "TMOS", name: "БПИФ акций" },
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
  onPresetsAdded?: () => void;
  paused: Set<string>;
  onTogglePause: (i: Instrument) => void;
  onScreener: () => void;
}

export function Watchlist({ market, items, selected, onSelect, onRemove, onQuickAdd, onPresetsAdded, paused, onTogglePause, onScreener }: Props) {
  const have = new Set(items.map((i) => i.symbol));
  const quick = QUICK[market].filter((q) => !have.has(q.symbol));

  return (
    <>
      <div className="panel-head">
        Избранное <span className="muted num">{items.length || ""}</span>
        {items.length > 0 && (
          <button className="btn small" onClick={onScreener}>
            Скринер
          </button>
        )}
      </div>
      <div className="scroll">
        {items.length === 0 && (
          <div className="empty">
            <b>Пока пусто.</b> Найдите тикер через поиск сверху (клавиша <kbd>/</kbd>) или добавьте один из популярных ниже.
          </div>
        )}
        <ul className="watch-list">
          {items.map((i) => (
            <li key={i.symbol} className={`watch-item${i.symbol === selected ? " active" : ""}`}>
              <button
                className="watch-select"
                aria-current={i.symbol === selected ? "true" : undefined}
                onClick={() => onSelect(i)}
              >
                <TickerIcon symbol={i.symbol} market={i.market} kind={i.kind} name={i.name} />
                <span className="sym">{i.symbol}</span>
                {i.name !== i.symbol && <span className="nm">{i.name}</span>}
              </button>
              <button
                className="btn ghost small"
                aria-pressed={!paused.has(`${i.market}|${i.symbol}`)}
                title={
                  paused.has(`${i.market}|${i.symbol}`)
                    ? "Сигналы по этой котировке не ищутся. Нажмите, чтобы следить"
                    : "Сигналы по этой котировке ищутся. Нажмите, чтобы выключить"
                }
                onClick={() => onTogglePause(i)}
              >
                {paused.has(`${i.market}|${i.symbol}`) ? "Не следим" : "Следим"}
              </button>
              <button
                className="icon-btn watch-remove"
                aria-label={`Убрать ${i.symbol} из избранного`}
                title="Убрать из избранного"
                onClick={() => onRemove(i)}
              >
                <Icon name="close" size={14} />
              </button>
            </li>
          ))}
        </ul>
        {quick.length > 0 && (
          <>
            <div className="panel-head subhead">Популярные</div>
            <div className="quick">
              {quick.map((q) => (
                <button key={q.symbol} className="btn small" onClick={() => onQuickAdd(q)}>
                  <TickerIcon symbol={q.symbol} market={q.market} size={16} /> {q.symbol}
                </button>
              ))}
            </div>
          </>
        )}
        <Presets market={market} onQuickAdd={onQuickAdd} onChanged={onPresetsAdded} />
      </div>
    </>
  );
}
