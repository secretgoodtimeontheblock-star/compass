import { useContext, useState } from "react";
import { api } from "../api";
import { AiContext, useAiRun } from "../lib/ai-context";
import { atLeast, useLevel } from "../lib/experience";
import type { AiStatus, Instrument } from "../types";
import { AiAnswer } from "./AiAnswer";

const BASICS = [
  "Что такое стоп-лосс и зачем он нужен?",
  "Как читать RSI?",
  "Чем акции рискованнее или безопаснее крипты?",
  "Почему советуют рисковать не больше 1–2% на сделку?",
  "Что значит «стратегия обошла „купил и держи“»?",
];

interface Props {
  instrument: Instrument | undefined;
  tf: string;
  status: AiStatus | undefined;
}

/** «Обучение»: вопросы новичка. С включённым контекстом модель видит показатели выбранного тикера. */
export function AiPanel({ instrument, tf, status }: Props) {
  const ctx = useContext(AiContext);
  const ai = useAiRun();
  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState("");
  const [withContext, setWithContext] = useState(true);
  const level = useLevel();
  const [facts, setFacts] = useState<{ title: string; text: string }>();
  const [factsError, setFactsError] = useState<string>();

  const ask = (q: string, refresh = false) => {
    const text = q.trim();
    if (text.length < 3) return;
    setAsked(text);
    const withData = withContext && instrument;
    void ai.run(
      (r) =>
        api.aiAsk(
          { question: text, ...(withData ? { market: instrument.market, symbol: instrument.symbol, tf } : {}) },
          r,
        ),
      refresh,
    );
  };

  const off = !status || status.provider === "off";

  // слой дисциплины: факты считает код (доступны и без AI), AI лишь пересказывает их
  const showFacts = async (title: string, load: () => Promise<{ text: string }>) => {
    setFactsError(undefined);
    try {
      setFacts({ title, text: (await load()).text });
    } catch (e) {
      setFacts(undefined);
      setFactsError(e instanceof Error ? e.message : "Не удалось получить факты");
    }
  };

  return (
    <div className="scroll">
      <div className="panel-head" style={{ paddingBottom: 2 }}>
        Спросить наставника
      </div>
      {off ? (
        <div className="empty" style={{ paddingTop: 4 }}>
          AI сейчас выключен. Он объясняет сигналы простыми словами, отвечает на вопросы новичка и разбирает журнал, но
          цены и размеры позиций считает приложение, а не модель.
          <div>
            <button className="btn primary" style={{ marginTop: 8 }} onClick={ctx.openSettings}>
              Включить AI
            </button>
          </div>
        </div>
      ) : (
        <div className="muted" style={{ padding: "0 12px 6px", fontSize: 12 }}>
          {status.provider} · {status.model ?? "модель по умолчанию"}
          {!status.available && <span className="down"> · недоступен: {status.reason}</span>}
          {status.available && status.reason && <span> · {status.reason}</span>}
        </div>
      )}

      {atLeast(level, "trader") && (
        <>
          <div className="panel-head" style={{ paddingBottom: 2 }}>Дисциплина</div>
          <div className="chips">
            <button className="btn small chip" disabled={ai.busy || off} onClick={() => (setAsked("Что изменилось за сутки?"), void ai.run((r) => api.aiWhatChanged(24, r)))}>
              Что изменилось за сутки?
            </button>
            <button className="btn small chip" disabled={ai.busy || off} onClick={() => (setAsked("Где я отступил от своих правил?"), void ai.run((r) => api.aiDiscipline(7, r)))}>
              Где я отступил от своих правил?
            </button>
            <button className="btn small chip" onClick={() => void showFacts("Что изменилось за сутки (факты)", () => api.changed(24))}>
              Факты без AI: что изменилось
            </button>
            <button className="btn small chip" onClick={() => void showFacts("Отступления от правил (факты)", () => api.violations(7))}>
              Факты без AI: правила
            </button>
          </div>
          {factsError && <div className="error">{factsError}</div>}
          {facts && (
            <div className="card">
              <b>{facts.title}</b>
              <pre className="num" style={{ whiteSpace: "pre-wrap", margin: "4px 0 0", font: "inherit" }}>{facts.text}</pre>
            </div>
          )}
        </>
      )}

      <div className="chips">
        {BASICS.map((q) => (
          <button key={q} className="btn small chip" disabled={ai.busy || off} onClick={() => (setQuestion(q), ask(q))}>
            {q}
          </button>
        ))}
      </div>

      <div className="form-grid" style={{ paddingTop: 4 }}>
        <label className="field full">
          <span>Свой вопрос</span>
          <textarea
            value={question}
            maxLength={500}
            placeholder="Например: что значит, что цена ниже SMA50?"
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) ask(question);
            }}
          />
        </label>
        {instrument && (
          <label className="full" style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
            <input type="checkbox" checked={withContext} onChange={(e) => setWithContext(e.target.checked)} />
            Учитывать данные по {instrument.symbol} ({tf})
          </label>
        )}
        <button className="btn primary full" disabled={ai.busy || off || question.trim().length < 3} onClick={() => ask(question)}>
          {ai.busy ? "Думаем…" : "Спросить"}
        </button>
      </div>

      {asked && (ai.result || ai.error || ai.busy) && (
        <div className="card">
          <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
            Вопрос: {asked}
          </div>
          <AiAnswer ai={ai} onRefresh={() => ask(asked, true)} />
        </div>
      )}
    </div>
  );
}
