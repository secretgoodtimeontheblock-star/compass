import type { AiStatus } from "../types";
import { Modal } from "./Modal";

const NAMES: Record<string, string> = { cursor: "Cursor CLI", claude: "Claude API", ollama: "Ollama" };

interface Props {
  status: AiStatus;
  onAccept: () => void;
  onDecline: () => void;
}

/** Первое обращение к облачному AI: явно говорим, что и куда уходит. */
export function ConsentDialog({ status, onAccept, onDecline }: Props) {
  return (
    <Modal title={`Отправить данные в ${NAMES[status.provider] ?? status.provider}?`} onClose={onDecline}>
      <p style={{ marginTop: 0 }}>Чтобы получить ответ, приложение отправит стороннему сервису:</p>
      <ul className="plain-list">
        <li>тикер, цены и показатели, которые посчитало приложение;</li>
        <li>для «Разобрать журнал» — ваши записи о сделках вместе с заметками.</li>
      </ul>
      <p className="muted small-text">
        Ключи, токены и файлы не отправляются. Согласие запоминается для этого провайдера; отозвать его можно,
        выключив AI в настройках. Локальная модель (Ollama) данные никуда не отправляет.
      </p>
      <div className="actions">
        <button className="btn" onClick={onDecline}>
          Не отправлять
        </button>
        <button className="btn primary" data-autofocus onClick={onAccept}>
          Согласен
        </button>
      </div>
    </Modal>
  );
}
