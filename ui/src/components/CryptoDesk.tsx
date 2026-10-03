import { useState } from "react";
import { api } from "../api";
import { fmtNum, fmtPrice } from "../lib/format";
import { useApi } from "../lib/use-api";
import type { Instrument } from "../types";

interface Props {
  instrument: Instrument;
  lastPrice: number | undefined;
}

/** Справка по монете, оповещение о цене, план равных покупок и оценка в рублях. */
export function CryptoDesk({ instrument, lastPrice }: Props) {
  const brief = useApi(() => api.cryptoBrief(instrument.symbol), [instrument.symbol], 60_000);
  const [budget, setBudget] = useState("1000");
  const [parts, setParts] = useState("4");
  const [prices, setPrices] = useState("");
  const [dca, setDca] = useState<string>();
  const [error, setError] = useState<string>();
  const [learn, setLearn] = useState(false);

  const calcDca = async () => {
    setError(undefined);
    try {
      const list = prices
        .split(/[\s,;]+/)
        .map((x) => Number(x))
        .filter((x) => x > 0);
      const res = await api.dca({
        budget: Number(budget),
        parts: Number(parts),
        prices: list.length ? list : undefined,
      });
      setDca(
        res.qty
          ? `Покупка по ${fmtNum(res.cash_each)} × ${res.parts}. Монет ${fmtNum(res.qty, 6)}, средняя ${fmtPrice(res.avg_price ?? 0)}, оценка сейчас ${fmtNum(res.value ?? 0)} (результат ${fmtNum(res.pnl ?? 0)}).`
          : `По ${fmtNum(res.cash_each)} × ${res.parts} покупок. ${res.note}`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось посчитать план");
    }
  };

  const rub =
    brief.data?.rub && brief.data.currency === "USDT" && lastPrice
      ? lastPrice * brief.data.rub.usd_rub
      : null;

  return (
    <div className="notice" style={{ marginTop: 8 }}>
      <b>Крипта · {brief.data?.exchange ?? "биржа"}</b>
      <p className="muted" style={{ margin: "4px 0" }}>
        Только спот и лонг. Стейкинг, фандинг и плечо здесь не считаются.
      </p>
      {brief.data?.warnings.map((w) => (
        <p key={w} style={{ margin: "4px 0" }}>{w}</p>
      ))}
      {brief.data?.spread && (
        <p className="muted" style={{ margin: "4px 0" }}>
          Спред стакана {fmtNum(brief.data.spread.spread_pct, 3)}% (bid {fmtPrice(brief.data.spread.bid)}, ask {fmtPrice(brief.data.spread.ask)}).
        </p>
      )}
      {brief.data?.btc_link && <p className="muted" style={{ margin: "4px 0" }}>{brief.data.btc_link}</p>}
      {rub !== null && brief.data?.rub && (
        <p style={{ margin: "4px 0" }}>
          ≈ {fmtNum(rub, 0)} ₽ по курсу ЦБ {fmtNum(brief.data.rub.usd_rub, 2)} ₽ за доллар ({brief.data.rub.as_of.slice(0, 10)}). {brief.data.rub.assumption}
        </p>
      )}
      {brief.error && <p className="down">{brief.error}</p>}

      <div className="row" style={{ gap: 6, flexWrap: "wrap", alignItems: "center", marginTop: 8 }}>
        <span className="muted">Равные покупки:</span>
        <input aria-label="Сумма плана" type="number" min={0} value={budget} style={{ width: 90 }} onChange={(e) => setBudget(e.target.value)} />
        <input aria-label="Число покупок" type="number" min={2} max={365} value={parts} style={{ width: 60 }} onChange={(e) => setParts(e.target.value)} />
        <input aria-label="Цены покупок" placeholder="цены через пробел, необязательно" value={prices} style={{ width: 220 }} onChange={(e) => setPrices(e.target.value)} />
        <button className="btn small" onClick={() => void calcDca()}>Посчитать</button>
      </div>
      {dca && <p style={{ margin: "4px 0" }}>{dca}</p>}
      {error && <p className="down">{error}</p>}
      <button className="btn ghost small" onClick={() => setLearn((v) => !v)}>{learn ? "Скрыть памятку" : "Памятка: биржа, ключи, памп"}</button>
      {learn && brief.data && (
        <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
          {brief.data.education.map((line) => <li key={line}>{line}</li>)}
          <li>{brief.data.fee_note}</li>
        </ul>
      )}
    </div>
  );
}
