import { Direction } from "@/lib/types";

const CLASS: Record<Direction, string> = {
  buy:     "badge-buy",
  sell:    "badge-sell",
  unknown: "badge-other",
};

const LABEL: Record<Direction, string> = {
  buy:     "Buy",
  sell:    "Sell",
  unknown: "Other",
};

export function DirectionBadge({ direction }: { direction: Direction }) {
  const d: Direction = direction in CLASS ? direction : "unknown";
  return <span className={CLASS[d]}>{LABEL[d]}</span>;
}
