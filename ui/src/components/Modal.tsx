import * as Dialog from "@radix-ui/react-dialog";
import { useRef, type ReactNode } from "react";
import { Icon } from "./Icon";

interface Props {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}

/**
 * Модальное окно на Radix Dialog: фокус заперт внутри, Esc закрывает, по закрытии фокус
 * возвращается на кнопку, открывшую окно. Элемент с data-autofocus получает фокус первым.
 */
export function Modal({ title, onClose, children, wide = false }: Props) {
  const returnFocus = useRef<HTMLElement | null>(null);
  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content
          className={wide ? "dialog dialog-wide" : "dialog"}
          aria-describedby={undefined}
          onOpenAutoFocus={(e) => {
            returnFocus.current = document.activeElement as HTMLElement;
            const target = (e.currentTarget as HTMLElement).querySelector<HTMLElement>("[data-autofocus]");
            if (target) {
              e.preventDefault();
              target.focus();
            }
          }}
          onCloseAutoFocus={(e) => {
            e.preventDefault();
            if (document.querySelector('[role="dialog"][data-state="open"]')) return;
            if (returnFocus.current?.isConnected) returnFocus.current.focus();
            else document.querySelector<HTMLElement>('.right.open [role="tab"][aria-selected="true"], .topbar [aria-label="Открыть палитру команд"]')?.focus();
          }}
        >
          <div className="dialog-head">
            <Dialog.Title>{title}</Dialog.Title>
            <Dialog.Close className="icon-btn" aria-label="Закрыть">
              <Icon name="close" size={18} />
            </Dialog.Close>
          </div>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
