import { Direction, TransactionType } from "@/lib/types";

const TYPE_LABEL: Record<string, string> = {
  buy:             "Buy",
  sell:            "Sell",
  grant:           "Grant",
  option_exercise: "Options",
  sell_to_cover:   "S-t-C",
  other:           "Other",
};

const TYPE_CLASS: Record<string, string> = {
  buy:             "badge-buy",
  sell:            "badge-sell",
  grant:           "badge-grant",
  option_exercise: "badge-options",
  sell_to_cover:   "badge-sell",
  other:           "badge-other",
};

interface Props {
  direction: Direction;
  transactionType?: TransactionType | null;
  needsReview?: boolean | null;
}

export function DirectionBadge({ direction, transactionType, needsReview }: Props) {
  // After migration: needs_review rows show an orange "Review" flag
  if (needsReview) {
    return <span className="badge-review">Review</span>;
  }

  // Use transaction_type when available; map unknown direction to "other"
  const key = transactionType ?? (direction === "unknown" ? "other" : direction);
  const cls = TYPE_CLASS[key] ?? "badge-other";
  const label = TYPE_LABEL[key] ?? "Other";
  return <span className={cls}>{label}</span>;
}
