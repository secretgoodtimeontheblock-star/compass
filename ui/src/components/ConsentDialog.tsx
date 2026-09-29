import { useEffect, useRef } from "react";
import type { AiStatus } from "../types";

const NAMES: Record<string, string> = { cursor: "Cursor CLI", claude: "Claude API", ollama: "Ollama" };

interface Props {
  status: AiStatus;
  onAccept: () => void;
  onDecline: () => void;
}

/** Первое обращение к облачному AI: явно говорим, что и куда уходит. */
export function ConsentDialog({ status, onAccept, onDecline }: Props) {
  const accept = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    accept.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onDecline();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onDecline]);

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onDecline()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-label="Согласие на отправку данных">
        <h2>Отправить данные в {NAMES[status.provider] ?? status.provider}?</h2>
        <p style={{ marginTop: 0 }}>Чтобы получить ответ, приложение отправит стороннему сервису:</p>
        <ul style={{ paddingLeft: 18 }}>
          <li>тикер, цены и показатели, которые посчитало приложение;</li>
          <li>для «Разобрать журнал» — ваши записи о сделках вместе с заметками.</li>
        </ul>
        <p className="muted" style={{ fontSize: 12 }}>
          Ключи, токены и файлы не отправляются. Согласие запоминается для этого провайдера; отозвать его можно,
          выключив AI в настройках. Локальная модель (Ollama) данные никуда не отправляет.
        </p>
        <div className="actions">
          <button className="btn" onClick={onDecline}>
            Не отправлять
          </button>
          <button className="btn primary" ref={accept} onClick={onAccept}>
            Согласен
          </button>
        </div>
      </div>
    </div>
  );
}
