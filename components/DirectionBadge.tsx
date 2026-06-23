"use client";

import { Direction, TransactionType } from "@/lib/types";
import { useT } from "./LanguageProvider";

const TYPE_CLASS: Record<string, string> = {
  buy:                    "badge-buy",
  sell:                   "badge-sell",
  grant:                  "badge-grant",
  option_exercise:        "badge-options",
  sell_to_cover:          "badge-sell",
  subscription:           "badge-buy",
  conversion:             "badge-other",
  inheritance:            "badge-other",
  gift_in:                "badge-other",
  gift_out:               "badge-other",
  transfer_in:            "badge-other",
  transfer_out:           "badge-other",
  pledge_or_security:     "badge-other",
  derivative_transaction: "badge-other",
  other:                  "badge-other",
  unknown:                "badge-review",
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

  const label: Record<string, string> = {
    buy:                    t("Acquisto",   "Buy"),
    sell:                   t("Vendita",    "Sell"),
    grant:                  t("Assegnaz.",  "Grant"),
    option_exercise:        t("Opzioni",    "Options"),
    sell_to_cover:          t("S-t-C",     "S-t-C"),
    subscription:           t("Sottoscr.", "Subscr."),
    conversion:             t("Convers.",  "Convert."),
    inheritance:            t("Success.",  "Inherit."),
    gift_in:                t("Donaz. in", "Gift in"),
    gift_out:               t("Donaz. out","Gift out"),
    transfer_in:            t("Trasf. in", "Transfer in"),
    transfer_out:           t("Trasf. out","Transfer out"),
    pledge_or_security:     t("Pegno",     "Pledge"),
    derivative_transaction: t("Derivato",  "Derivative"),
    other:                  t("Altro",     "Other"),
    unknown:                t("Sconos.",   "Unknown"),
  };
  const displayLabel = label[key] ?? t("Altro", "Other");

  return <span className={cls}>{displayLabel}</span>;
}
