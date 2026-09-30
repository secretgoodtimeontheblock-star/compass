import { useEffect, useState } from "react";
import { api } from "../api";
import type { AiModel, AiProviderId, Market, Settings } from "../types";
import { AccountsSection } from "./AccountsSection";
import { Modal } from "./Modal";

interface Props {
  settings: Settings;
  markets: Market[];
  onClose: () => void;
  onSaved: (s: Settings) => void;
}

export function SettingsDialog({ settings, markets, onClose, onSaved }: Props) {
  const [draft, setDraft] = useState({
    capital: String(settings.capital),
    scan_interval_min: String(settings.scan_interval_min),
    tf_moex: settings.tf_moex,
    tf_crypto: settings.tf_crypto,
  });
  const [aiProvider, setAiProvider] = useState<AiProviderId>(settings.ai_provider);
  const [aiModels, setAiModels] = useState<Partial<Record<AiProviderId, string>>>(settings.ai_models ?? {});
  const [modelList, setModelList] = useState<AiModel[]>([]);
  const [modelsBusy, setModelsBusy] = useState(false);
  const [modelsError, setModelsError] = useState<string>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  // список моделей выбранного провайдера (у Cursor это вызов CLI — несколько секунд)
  useEffect(() => {
    setModelList([]);
    setModelsError(undefined);
    if (aiProvider === "off") return;
    let stale = false;
    setModelsBusy(true);
    api
      .aiModels(aiProvider)
      .then((m) => !stale && setModelList(m))
      .catch((e) => !stale && setModelsError(e instanceof Error ? e.message : "Не удалось получить модели"))
      .finally(() => !stale && setModelsBusy(false));
    return () => {
      stale = true;
    };
  }, [aiProvider]);

  const tfs = (id: string) => markets.find((m) => m.id === id)?.timeframes ?? [];

  const save = async () => {
    setBusy(true);
    setError(undefined);
    try {
      onSaved(
        await api.saveSettings({
          capital: Number(draft.capital),
          scan_interval_min: Number(draft.scan_interval_min),
          tf_moex: draft.tf_moex,
          tf_crypto: draft.tf_crypto,
          ai_provider: aiProvider,
          ai_models: aiModels,
        }),
      );
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="Настройки" onClose={onClose}>
        <div className="form-grid" style={{ padding: 0 }}>
          <label className="field">
            <span>Стартовый капитал бэктеста</span>
            <input
              data-autofocus
              type="number"
              min={1}
              value={draft.capital}
              onChange={(e) => setDraft({ ...draft, capital: e.target.value })}
            />
          </label>
          <label className="field">
            <span>Таймфрейм сигналов: акции</span>
            <select value={draft.tf_moex} onChange={(e) => setDraft({ ...draft, tf_moex: e.target.value })}>
              {tfs("moex").map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Таймфрейм сигналов: крипта</span>
            <select value={draft.tf_crypto} onChange={(e) => setDraft({ ...draft, tf_crypto: e.target.value })}>
              {tfs("crypto").map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </label>
          <label className="field full">
            <span>Как часто искать сигналы, минут</span>
            <input
              type="number"
              min={1}
              max={1440}
              value={draft.scan_interval_min}
              onChange={(e) => setDraft({ ...draft, scan_interval_min: e.target.value })}
            />
          </label>
        </div>
        <AccountsSection />
        <p className="muted small-text">
          Риск 1–2% на сделку — общепринятая осторожная планка: размер позиции подбирается так, чтобы срабатывание
          стопа стоило не больше этой доли капитала счёта.
        </p>

        <h3 className="dialog-section">AI-помощник</h3>
        <div className="form-grid" style={{ padding: 0 }}>
          <label className="field">
            <span>Провайдер</span>
            <select value={aiProvider} onChange={(e) => setAiProvider(e.target.value as AiProviderId)}>
              <option value="off">Выключен</option>
              <option value="cursor">Cursor CLI</option>
              <option value="claude">Claude API</option>
              <option value="ollama">Ollama (локально)</option>
            </select>
          </label>
          <label className="field">
            <span>Модель{modelsBusy ? " (загрузка…)" : ""}</span>
            <select
              disabled={aiProvider === "off" || modelsBusy}
              value={aiProvider === "off" ? "" : (aiModels[aiProvider] ?? "")}
              onChange={(e) => aiProvider !== "off" && setAiModels({ ...aiModels, [aiProvider]: e.target.value })}
            >
              <option value="">По умолчанию</option>
              {aiProvider !== "off" && aiModels[aiProvider] && !modelList.some((m) => m.id === aiModels[aiProvider]) && (
                <option value={aiModels[aiProvider]}>{aiModels[aiProvider]}</option>
              )}
              {modelList.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        {modelsError && <div className="notice" style={{ margin: "8px 0 0" }}>{modelsError}</div>}
        <p className="muted small-text">
          {aiProvider === "off" && "AI выключен: приложение работает без него."}
          {aiProvider === "cursor" && "Cursor CLI работает в режиме «только чтение». Ответ занимает 15–30 секунд, нужен вход: cursor-agent login. Данные уходят в облако Cursor."}
          {aiProvider === "claude" && "Нужен ключ ANTHROPIC_API_KEY в окружении (или вход через ant auth login). Данные уходят в Anthropic; оплата по тарифам API."}
          {aiProvider === "ollama" && "Работает локально, данные не покидают компьютер. Нужна запущенная Ollama и скачанная модель."}
          {aiProvider !== "off" && " Цены и размеры позиций считает приложение, а не модель."}
        </p>
        {error && <div className="error" style={{ margin: "8px 0 0" }}>{error}</div>}
        <div className="actions">
          <button className="btn" onClick={onClose}>
            Отмена
          </button>
          <button className="btn primary" disabled={busy} onClick={save}>
            Сохранить
          </button>
        </div>
    </Modal>
  );
}
