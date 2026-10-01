import { createContext, useContext } from "react";
import type { ExperienceLevel } from "../types";

/**
 * Три уровня — три представления ОДНОГО движка: расчёты и проверки у всех одинаковые,
 * различается только то, сколько из них выведено на экран сразу.
 */
export const LEVEL_LABEL: Record<ExperienceLevel, string> = {
  beginner: "Начинаю",
  trader: "Торгую",
  researcher: "Исследую",
};

export const LEVEL_HINT: Record<ExperienceLevel, string> = {
  beginner: "График, сигналы, риск в деньгах, размер позиции, стоп, учебная сделка и простой журнал. Сложная статистика скрыта.",
  trader: "Добавляет состояние счёта, планы, дневные лимиты, суммарный риск, сверку план/факт и недельный разбор.",
  researcher: "Добавляет лабораторию проверки, walk-forward, карты параметров, Sharpe/Sortino, историю экспериментов.",
};

const RANK: Record<ExperienceLevel, number> = { beginner: 0, trader: 1, researcher: 2 };

export function atLeast(level: ExperienceLevel, min: ExperienceLevel): boolean {
  return RANK[level] >= RANK[min];
}

/** Без провайдера (например, в изолированных тестах) показывается всё: скрытие — решение приложения. */
export const ExperienceContext = createContext<ExperienceLevel>("researcher");

export function useLevel(): ExperienceLevel {
  return useContext(ExperienceContext);
}
