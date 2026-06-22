"use client";

import { Direction, TransactionType } from "@/lib/types";
import { useT } from "./LanguageProvider";

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
  const t = useT();

  if (needsReview) {
    return <span className="badge-review">{t("Verifica", "Review")}</span>;
  }

  const key = transactionType ?? (direction === "unknown" ? "other" : direction);
  const cls = TYPE_CLASS[key] ?? "badge-other";

  const label = {
    buy:             t("Acquisto",   "Buy"),
    sell:            t("Vendita",    "Sell"),
    grant:           t("Assegnaz.",  "Grant"),
    option_exercise: t("Opzioni",    "Options"),
    sell_to_cover:   t("S-t-C",     "S-t-C"),
    other:           t("Altro",      "Other"),
  }[key] ?? t("Altro", "Other");

  return <span className={cls}>{label}</span>;
}
