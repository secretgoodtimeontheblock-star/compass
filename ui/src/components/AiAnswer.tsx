import { useContext } from "react";
import { AiContext, type AiRun } from "../lib/ai-context";
import { parseAnswer, type Seg } from "../lib/ai-text";

const Segs = ({ segs }: { segs: Seg[] }) => (
  <>
    {segs.map((s, i) => (s.bold ? <strong key={i}>{s.text}</strong> : <span key={i}>{s.text}</span>))}
  </>
);

/** Ответ AI: текст, предупреждение о непроверенных числах, происхождение ответа и ошибка. */
export function AiAnswer({ ai, onRefresh }: { ai: AiRun; onRefresh?: () => void }) {
  const ctx = useContext(AiContext);
  const { result, error, errorCode, busy } = ai;

  return (
    <div className="ai-answer" aria-live="polite">
      {busy && (
        <div className="ai-busy">
          <span className="spinner" aria-hidden="true" /> AI думает… обычно 15–30 секунд
        </div>
      )}
      {error && (
        <div className="error" style={{ margin: "8px 0" }}>
          {error}
          {(errorCode === "off" || errorCode === "no_model" || errorCode === "unavailable") && (
            <div>
              <button className="btn small" style={{ marginTop: 6 }} onClick={ctx.openSettings}>
                Открыть настройки AI
              </button>
            </div>
          )}
        </div>
      )}
      {result && !busy && (
        <>
          <div className="ai-text">
            {parseAnswer(result.text).map((b, i) =>
              b.kind === "p" ? (
                <p key={i}>
                  <Segs segs={b.segs} />
                </p>
              ) : (
                <ul key={i}>
                  {b.items.map((it, j) => (
                    <li key={j}>
                      <Segs segs={it} />
                    </li>
                  ))}
                </ul>
              ),
            )}
          </div>
          {result.warnings.map((w) => (
            <div className="notice" style={{ margin: "8px 0" }} key={w}>
              {w}
            </div>
          ))}
          <div className="ai-foot muted">
            Ответ сгенерирован AI ({result.provider} · {result.model}
            {result.cached ? " · из кэша" : ""}) и может содержать ошибки. Это не инвестиционная рекомендация.
            {onRefresh && (
              <button className="btn ghost small" onClick={onRefresh}>
                Обновить ответ
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
