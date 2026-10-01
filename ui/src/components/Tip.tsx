import * as Tooltip from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";

/** Подсказка для кнопок-иконок: появляется при наведении и при фокусе с клавиатуры. */
export function Tip({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content className="tooltip" sideOffset={6}>
          {label}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

export const TipProvider = Tooltip.Provider;
