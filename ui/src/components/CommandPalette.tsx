import { Command } from "cmdk";
import type { Instrument } from "../types";
import { Icon, type IconName } from "./Icon";
import { Modal } from "./Modal";

export interface PaletteAction {
  id: string;
  label: string;
  icon: IconName;
  hint?: string;
  run: () => void;
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  actions: PaletteAction[];
  instruments: Instrument[];
  onPick: (i: Instrument) => void;
}

/** Палитра команд (Ctrl/⌘ + K): быстрый переход к тикеру и основным действиям без мыши. */
export function CommandPalette({ open, onOpenChange, actions, instruments, onPick }: Props) {
  const close = () => onOpenChange(false);
  if (!open) return null;
  return (
    <Modal title="Быстрые действия" onClose={close}>
    <Command label="Быстрые действия" className="command-menu">
      <div className="palette-input">
        <Icon name="search" size={16} />
        <Command.Input data-autofocus placeholder="Тикер или действие…" />
        <kbd>Esc</kbd>
      </div>
      <Command.List>
        <Command.Empty>Ничего не найдено</Command.Empty>
        {instruments.length > 0 && (
          <Command.Group heading="Избранное">
            {instruments.map((i) => (
              <Command.Item
                key={`${i.market}:${i.symbol}`}
                value={`${i.symbol} ${i.name}`}
                onSelect={() => {
                  onPick(i);
                  close();
                }}
              >
                <Icon name="chart" />
                <b>{i.symbol}</b>
                {i.name !== i.symbol && <span className="muted">{i.name}</span>}
                <span className="palette-hint">{i.market === "moex" ? "МосБиржа" : "Крипта"}</span>
              </Command.Item>
            ))}
          </Command.Group>
        )}
        <Command.Group heading="Действия">
          {actions.map((a) => (
            <Command.Item
              key={a.id}
              value={a.label}
              onSelect={() => {
                close();
                a.run();
              }}
            >
              <Icon name={a.icon} />
              <span>{a.label}</span>
              {a.hint && <span className="palette-hint">{a.hint}</span>}
            </Command.Item>
          ))}
        </Command.Group>
      </Command.List>
    </Command>
    </Modal>
  );
}
