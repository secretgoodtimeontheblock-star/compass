import type { ApiState } from "../lib/use-api";

export function RequestStatus({ state, label }: { state: ApiState<unknown>; label: string }) {
  if (state.error) return <div className="error" role="alert"><b>{label}</b><p>{state.error}</p><button className="btn small" onClick={state.reload}>Повторить</button></div>;
  if (state.loading && state.data === undefined) return <div className="empty" role="status">Загружаем…</div>;
  return null;
}
