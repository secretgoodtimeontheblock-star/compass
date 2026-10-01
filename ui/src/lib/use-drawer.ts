import { useEffect, useRef, useState } from "react";

export function useDrawer(open: boolean, close: () => void) {
  const [narrow, setNarrow] = useState(() => matchMedia("(max-width: 1150px)").matches);
  const panel = useRef<HTMLElement>(null);
  const onClose = useRef(close);
  onClose.current = close;
  useEffect(() => {
    const query = matchMedia("(max-width: 1150px)");
    const update = () => setNarrow(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  const active = narrow && open;
  useEffect(() => {
    if (!active) return;
    const previous = document.activeElement as HTMLElement;
    const background = [...document.querySelectorAll<HTMLElement>(".topbar, .side, .center, .footer")];
    const oldInert = background.map((el) => el.inert);
    background.forEach((el) => { el.inert = true; });
    panel.current?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.focus();
    const escape = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented && !document.querySelector('[role="dialog"][data-state="open"]')) {
        e.preventDefault();
        onClose.current();
      }
    };
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("keydown", escape);
      background.forEach((el, i) => { el.inert = oldInert[i]; });
      if (previous.isConnected) previous.focus();
    };
  }, [active]);
  return { panel, active };
}
