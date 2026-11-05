"use client";

import { useState } from "react";
import { errorMessage, type Txn } from "@/lib/api";
import { formatCents, formatDate, lineLabel } from "@/lib/format";

type Line = { category: string; name: string; group?: string };

// a month's transactions with the budget line each counts against (tpc-22). plaid's category applies on its
// own; the line picker corrects one transaction, then offers to remember it for that merchant
export default function TransactionsTable({ transactions, lines, onCorrect, onRemembered }: {
  transactions: Txn[];
  lines: Line[];
  onCorrect: (id: number, category: string, remember: boolean) => Promise<Txn>;
  onRemembered: () => void; // a rule moved the merchant's other rows too; the caller reloads them
}) {
  const [rows, setRows] = useState(transactions);
  const [offer, setOffer] = useState<{ id: number; merchant: string; category: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [prev, setPrev] = useState(transactions);
  if (prev !== transactions) {
    setPrev(transactions);
    setRows(transactions);
  }
  // a goal line has no spending to hold, and the server refuses a transaction moved onto one
  const spendable = lines.filter((l) => l.group !== "goal");
  const choices = spendable.some((l) => l.category === "FREE") ? spendable : [...spendable, { category: "FREE", name: "Free" }];
  const lined = new Set(choices.map((l) => l.category));

  async function pick(t: Txn, category: string, remember: boolean) {
    setError(null);
    try {
      const updated = await onCorrect(t.id, category, remember);
      setRows((rs) => rs.map((r) => (r.id === updated.id ? updated : r)));
      if (remember) {
        setOffer(null);
        onRemembered();
      } else {
        setOffer(t.merchant ? { id: t.id, merchant: t.merchant, category } : null);
      }
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  if (rows.length === 0) return <p className="text-sm text-neutral-500">No transactions this month.</p>;
  return (
    <div className="space-y-2">
      {error && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{error}</p>}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[32rem] text-sm">
          <thead>
            <tr className="text-left text-neutral-500">
              <th className="py-2 pr-4 font-medium">Date</th>
              <th className="py-2 pr-4 font-medium">Merchant</th>
              <th className="py-2 pr-4 font-medium">Line</th>
              <th className="py-2 text-right font-medium">Amount</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.id} className="border-t border-black/10 dark:border-white/15">
                <td className="py-2 pr-4">{formatDate(t.date)}</td>
                <td className="py-2 pr-4">{t.merchant ?? "—"}</td>
                <td className="py-2 pr-4">
                  {t.line_name === null ? (
                    <span className="text-neutral-500">—</span>
                  ) : (
                    <span className="flex flex-wrap items-center gap-2">
                      <select aria-label={`Line for ${t.merchant ?? "this transaction"}`}
                        value={lined.has(t.category) ? t.category : "FREE"}
                        onChange={(e) => pick(t, e.target.value, false)}
                        className="rounded border border-black/20 bg-transparent px-1 py-0.5 dark:border-white/30">
                        {choices.map((l) => <option key={l.category} value={l.category}>{lineLabel(l)}</option>)}
                      </select>
                      {t.source === "rule" && <span className="text-xs text-neutral-500">rule</span>}
                      {offer?.id === t.id && (
                        <button type="button" onClick={() => pick(t, offer.category, true)}
                          className="text-xs underline underline-offset-2">
                          Always for {offer.merchant}?
                        </button>
                      )}
                    </span>
                  )}
                </td>
                <td className="py-2 text-right tabular-nums">{formatCents(t.amount_cents)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
