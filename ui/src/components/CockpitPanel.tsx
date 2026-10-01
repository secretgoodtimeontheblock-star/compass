import { useState } from "react";
import { api } from "../api";
import { atLeast, useLevel } from "../lib/experience";
import { useApi } from "../lib/use-api";
import type { CockpitQuestion, CockpitResponse } from "../types";
import { Learn } from "./Learn";

const STATUS_CLASS: Record<CockpitResponse["status"], string> = { ok: "notice", attention: "notice", stop: "error" };
const STATUS_MARK: Record<CockpitQuestion["status"], string> = { ok: "✓", attention: "!", bad: "✕" };

/**
 * Кокпит решений: один ответ на вопросы «что происходит, что у меня, сколько рискую, что активно,
 * не нарушаю ли правила, свежи ли данные». Новичку — заголовок и пункты внимания; «Торгую» и выше —
 * ещё и все семь вопросов.
 */
export function CockpitPanel({ refreshKey = 0 }: { refreshKey?: number }) {
  const level = useLevel();
  const api_ = useApi(() => api.cockpit(), [refreshKey], 60_000);
  const [open, setOpen] = useState(false);
  const c = api_.data;
  if (!c) return api_.error ? <div className="notice" style={{ margin: "6px 12px" }}>Кокпит недоступен: {api_.error}</div> : null;
  const full = atLeast(level, "trader");
  return (
    <div style={{ padding: "6px 12px 0" }} data-testid="cockpit">
      <div className="panel-head" style={{ padding: "0 0 4px" }}>
        Сегодня
        {full && (
          <button className="btn small" aria-expanded={open} onClick={() => setOpen((v) => !v)} style={{ marginLeft: 8 }}>
            {open ? "Скрыть вопросы" : "Все вопросы дня"}
          </button>
        )}
      </div>
      <div className={STATUS_CLASS[c.status]} role="status" style={{ marginBottom: 6 }}>
        <b>{c.status === "stop" ? "Стоп на сегодня. " : ""}</b>
        {c.headline}
      </div>
      {c.attention
        .filter((a) => a.text !== c.headline)
        .slice(0, full ? 8 : 3)
        .map((a) => (
          <div key={`${a.section}:${a.text}`} className={a.severity === "stop" || a.severity === "bad" ? "error" : "notice"} style={{ marginBottom: 4 }}>
            {a.text}
            {a.hint && <span className="muted"> {a.hint}</span>}
            <Learn ids={a.learn} />
          </div>
        ))}
      {full && open && (
        <div className="card">
          {c.questions.map((q) => (
            <div key={q.id} style={{ marginBottom: 8 }}>
              <div className="row">
                <b>{q.question}</b>
                <span className={q.status === "ok" ? "up" : "down"} aria-label={q.status}>{STATUS_MARK[q.status]}</span>
              </div>
              <div className="muted">{q.answer}</div>
            </div>
          ))}
          <p className="caveat" style={{ margin: 0 }}>{c.notes.join(" ")}</p>
        </div>
      )}
    </div>
  );
}
