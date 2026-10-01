import { useEffect, useState } from "react";
import { api } from "../api";
import type { GlossaryTerm } from "../types";
import { Modal } from "./Modal";

let cache: Promise<GlossaryTerm[]> | undefined;
function glossary(): Promise<GlossaryTerm[]> {
  cache ??= api.glossary().catch((e) => {
    cache = undefined; // не запоминаем сбой: следующий клик попробует снова
    throw e;
  });
  return cache;
}

/** «Что это?» рядом с находкой: короткие определения понятий, которые нужны именно в этот момент. */
export function Learn({ ids }: { ids: string[] | undefined }) {
  const [open, setOpen] = useState<string>();
  if (!ids || ids.length === 0) return null;
  return (
    <span className="learn" style={{ display: "inline-flex", gap: 4, flexWrap: "wrap", marginLeft: 6 }}>
      {ids.map((id) => (
        <button key={id} type="button" className="chip" onClick={() => setOpen(id)} aria-label={`Что такое: ${id}`}>
          ? {id.replaceAll("_", " ")}
        </button>
      ))}
      {open && <TermDialog id={open} onClose={() => setOpen(undefined)} />}
    </span>
  );
}

function TermDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const [term, setTerm] = useState<GlossaryTerm | null>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    let live = true;
    glossary()
      .then((all) => live && setTerm(all.find((t) => t.id === id) ?? null))
      .catch((e) => live && setError(e instanceof Error ? e.message : "Не удалось загрузить глоссарий"));
    return () => { live = false; };
  }, [id]);
  return (
    <Modal title={term?.term ?? "Понятие"} onClose={onClose}>
      {error && <div className="error">{error}</div>}
      {term === undefined && !error && <p className="muted">Загрузка…</p>}
      {term === null && <p className="muted">Такого понятия нет в глоссарии.</p>}
      {term && (
        <>
          <p>{term.short}</p>
          <p className="caveat" style={{ marginBottom: 0 }}><b>Почему это важно.</b> {term.why}</p>
        </>
      )}
      <div className="actions">
        <button className="btn" data-autofocus onClick={onClose}>Понятно</button>
      </div>
    </Modal>
  );
}
