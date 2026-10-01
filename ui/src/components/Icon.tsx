import {
  Activity,
  BookOpen,
  ChartCandlestick,
  CircleHelp,
  Command,
  Compass,
  FlaskConical,
  History,
  Inbox,
  Moon,
  PanelRight,
  Plus,
  RefreshCw,
  Search,
  Settings,
  Sparkles,
  Sun,
  X,
  type LucideIcon,
} from "lucide-react";

const ICONS = {
  sun: Sun,
  moon: Moon,
  settings: Settings,
  close: X,
  panel: PanelRight,
  help: CircleHelp,
  search: Search,
  scan: RefreshCw,
  plus: Plus,
  command: Command,
  compass: Compass,
  signals: Activity,
  backtest: FlaskConical,
  journal: BookOpen,
  ai: Sparkles,
  inbox: Inbox,
  chart: ChartCandlestick,
  replay: History,
} satisfies Record<string, LucideIcon>;

export type IconName = keyof typeof ICONS;

/** Иконки lucide: наследуют цвет текста, для скринридера скрыты — подпись даёт сама кнопка. */
export function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  const Glyph = ICONS[name];
  return <Glyph className="icon" size={size} strokeWidth={1.9} aria-hidden="true" />;
}
