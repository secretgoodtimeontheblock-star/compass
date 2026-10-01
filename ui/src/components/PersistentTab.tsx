import * as Tabs from "@radix-ui/react-tabs";
import { useRef, Suspense, type ReactNode } from "react";
import { ErrorBoundary } from "./ErrorBoundary";

/** Посещённые формы остаются смонтированы: смена вкладки не стирает ввод. */
export function PersistentTab({ value, active, children }: { value: string; active: string; children: ReactNode }) {
  const visited = useRef(false);
  if (value === active) visited.current = true;
  if (!visited.current) return null;
  return <Tabs.Content value={value} forceMount hidden={value !== active} className="tabpanel"><ErrorBoundary><Suspense fallback={<div className="empty" role="status">Открываем раздел…</div>}>{children}</Suspense></ErrorBoundary></Tabs.Content>;
}
