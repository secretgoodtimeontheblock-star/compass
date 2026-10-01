import { useState } from "react";
import { api } from "../api";
import { useApi } from "../lib/use-api";
import type { Instrument, MarketId } from "../types";
import { Icon, type IconName } from "./Icon";
import { TickerIcon } from "./TickerIcon";

interface Props {
  market: MarketId;
  onQuickAdd: (i: Instrument) => void;
  onChanged?: () => void;
}

const KNOWN: ReadonlySet<string> = new Set([
  "star", "bank", "fuel", "pickaxe", "cart", "cpu", "bolt", "radio", "flask", "plane", "building", "layers",
  "coins", "blocks", "network", "brain", "smile",
]);

/** Готовые наборы по темам: сектора акций и категории монет. Подборка для знакомства, а не рекомендация. */
export function Presets({ market, onQuickAdd, onChanged }: Props) {
  const groups = useApi(() => api.presets(market), [market]);
  const [open, setOpen] = useState<string>();
  const [note, setNote] = useState<string>();
  const [busy, setBusy] = useState(false);

  if (groups.error) return <div className="notice" style={{ margin: "6px 12px" }}>Наборы недоступны: {groups.error}</div>;
  if (!groups.data || groups.data.length === 0) return null;

  const addAll = async (id: string) => {
    setBusy(true);
    try {
      const r = await api.addPreset(market, id);
      setNote(r.added > 0 ? `«${r.title}»: добавлено ${r.added}${r.already ? `, уже были ${r.already}` : ""}.` : `«${r.title}»: всё уже в избранном.`);
      groups.reload();
      onChanged?.();
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Не удалось добавить набор");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="presets" data-testid="presets">
      <div className="panel-head subhead">Наборы по темам</div>
      {note && <div className="notice" role="status" style={{ margin: "0 12px 6px" }}>{note}</div>}
      {groups.data.map((g) => {
        const isOpen = open === g.id;
        const left = g.instruments.filter((i) => !i.watched).length;
        return (
          <div key={g.id} className="preset-group">
            <button className="preset-head" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? undefined : g.id)}>
              <Icon name={(KNOWN.has(g.icon) ? g.icon : "star") as IconName} size={16} />
              <span>{g.title}</span>
              <span className="muted num count">{g.instruments.length}</span>
              <span className="chev"><Icon name="down" size={14} /></span>
            </button>
            {isOpen && (
              <div className="preset-body">
                <div className="muted" style={{ fontSize: 12 }}>{g.note}</div>
                <div className="preset-chips">
                  {g.instruments.map((i) => (
                    <button
                      key={i.symbol}
                      className="btn small"
                      aria-pressed={i.watched}
                      title={i.watched ? "Уже в избранном" : i.name}
                      disabled={i.watched}
                      onClick={() => onQuickAdd(i)}
                    >
                      <TickerIcon symbol={i.symbol} market={i.market} kind={i.kind} name={i.name} size={16} /> {i.symbol}
                    </button>
                  ))}
                </div>
                <button className="btn small primary" style={{ marginTop: 8 }} disabled={busy || left === 0} onClick={() => addAll(g.id)}>
                  {left === 0 ? "Всё уже в избранном" : `Добавить все (${left})`}
                </button>
              </div>
            )}
          </div>
        );
      })}
      <p className="caveat" style={{ margin: "6px 12px" }}>
        Это подборка для знакомства, а не рекомендация. Другие тикеры ищите через поиск сверху.
      </p>
    </div>
  );
}
