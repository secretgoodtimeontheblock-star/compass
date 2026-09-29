import { useState } from "react";
import type { Instrument } from "../types";

interface Props {
  selected?: Instrument;
  onPick: (i: Instrument) => Promise<void>;
  onBacktest: () => void;
  onLearn: () => void;
  onClose: () => void;
}

export function GettingStarted({ selected, onPick, onBacktest, onLearn, onClose }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const start = async () => {
    setBusy(true);
    setError("");
    try {
      await onPick({ market: "crypto", symbol: "BTC/USDT", name: "BTC/USDT" });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось открыть пример. Попробуйте ещё раз.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="getting-started" aria-labelledby="start-title">
      <div className="getting-started-head">
        <h2 id="start-title">Начните с наблюдения</h2>
        <button className="btn ghost" onClick={onClose} aria-label="Скрыть подсказку для начала работы">Скрыть</button>
      </div>
      <p>Счёт и подписка не нужны. Compass показывает рынок и помогает изучать правила — реальные сделки здесь не совершаются.</p>
      <ol>
        <li><b>Откройте пример.</b> BTC/USDT — цена биткоина в USDT на OKX. Это пример для знакомства, не совет купить.</li>
        <li><b>Проверьте правило.</b> Во вкладке «Проверка» видно, как стратегия работала на прошлых свечах с учётом расходов.</li>
        <li><b>Разберите результат.</b> Сигнал — срабатывание правила, а не прогноз или гарантия прибыли.</li>
      </ol>
      <div className="getting-started-actions">
        <button className="btn primary" onClick={() => void start()} disabled={busy} aria-busy={busy}>Открыть BTC/USDT</button>
        <button className="btn" onClick={onBacktest} disabled={!selected}>Проверить на истории</button>
        <button className="btn" onClick={onLearn}>Как устроен AI</button>
      </div>
      {error && <p role="alert" className="down">{error}</p>}
      <details>
        <summary>Что бесплатно и с чего пока не стоит начинать</summary>
        <p>Криптопоток OKX и история МосБиржи не требуют подписки. Данные акций задержаны на 15 минут. AI по умолчанию выключен: облачные провайдеры могут требовать оплаты, локальная Ollama — отдельной установки.</p>
        <p>Проверка на истории не повторяет реальное исполнение: стопы пока не моделируются. Журнал предназначен для ручных записей; автоматического учебного счёта пока нет.</p>
      </details>
    </section>
  );
}
