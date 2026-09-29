import type { LiveState, Market } from "../types";

interface Props {
  market?: Market;
  state?: LiveState;
  message?: string;
  receivedAt?: number;
  fetchedAt?: number | null;
  stale: boolean;
  hasData: boolean;
  gap: boolean;
  onRetry: () => void;
}

export function DataStatus({ market, state, message, receivedAt, fetchedAt, stale, hasData, gap, onRetry }: Props) {
  const live = state === "live";
  const delayed = market?.id === "moex";
  const label = delayed ? "МосБиржа · задержка 15 мин"
    : live ? "OKX · живой поток" : market?.live_supported ? "OKX · поток не готов" : `${market?.name ?? "Источник"} · периодическое обновление`;
  const at = live ? receivedAt : fetchedAt ? fetchedAt * 1000 : undefined;
  const time = at ? new Date(at).toLocaleTimeString("ru-RU") : undefined;
  return (
    <section className="data-status" aria-label="Источник и актуальность данных">
      <div className="data-status-row">
        <span className={delayed ? "source-label delayed" : live ? "source-label live" : "source-label"}>
          <span className="source-dot" aria-hidden="true" />{label}
        </span>
        {time && <span className="muted num">{live ? "Получено" : "История обновлена"} в {time}</span>}
        {(state === "unavailable" || state === "reconnecting" || stale) && (
          <button className="btn small" onClick={onRetry}>Повторить подключение</button>
        )}
      </div>
      <div className="data-detail" role="status">
        {delayed ? "Бесплатный источник. Цена отстаёт от торгов; используйте для изучения истории."
          : live ? "Публичные данные без счёта и ключей. Текущая свеча ещё может меняться."
          : message ?? (market?.live_supported ? "Выберите криптопару — поток подключится автоматически." : "История запрашивается раз в минуту.")}
        {stale && " Историю не удалось обновить — показан сохранённый кэш."}
        {!hasData && " Ожидаем данные графика."}
        {gap && " В истории есть пропуски. Не используйте этот участок для проверки стратегии."}
      </div>
    </section>
  );
}
