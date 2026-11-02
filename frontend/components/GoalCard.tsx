import type { Goal } from "@/lib/api";
import { formatCents, formatDate, formatMonth } from "@/lib/format";

const KINDS: Record<Goal["kind"], string> = { debt_payoff: "Debt payoff", emergency_fund: "Emergency fund", savings: "Savings" };

export function goalTitle(goal: Goal): string {
  return `${KINDS[goal.kind] ?? goal.kind}${goal.strategy ? ` (${goal.strategy})` : ""}`;
}

// one goal from the goal agent. a proposal gets Accept / Dismiss; an accepted goal gets Stop, which ends its budget
// line at the next close. a proposal with no start date (from before tpc-25) can't be accepted, so it only gets Dismiss
export default function GoalCard({ goal, busy, onAccept, onDismiss }: {
  goal: Goal;
  busy: boolean;
  onAccept: (id: number) => void;
  onDismiss: (id: number) => void;
}) {
  const proposed = goal.status === "proposed";
  return (
    <article className="space-y-2 rounded-lg border border-black/10 p-4 dark:border-white/15">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-semibold">{goalTitle(goal)}</h3>
        <span className="tabular-nums">{formatCents(goal.monthly_cents)}/mo</span>
      </header>
      <p className="text-sm text-neutral-500">
        Target {formatCents(goal.target_cents)}{goal.target_date && ` by ${formatDate(goal.target_date)}`}
      </p>
      <p>{goal.rationale}</p>
      {!proposed && goal.start_date && <p className="text-sm">In your budget from {formatMonth(goal.start_date)}</p>}
      {proposed && goal.start_date && (
        <p className="text-sm text-neutral-500">Joins your budget in {formatMonth(goal.start_date)} if you accept.</p>
      )}
      {proposed && !goal.start_date && (
        <p className="text-sm text-neutral-500">This proposal is too old to accept. Generate goals again.</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {proposed && goal.start_date && (
          <button type="button" disabled={busy} onClick={() => onAccept(goal.id)}
            className="rounded bg-black px-3 py-1 text-sm text-white disabled:opacity-50 dark:bg-white dark:text-black">
            Accept
          </button>
        )}
        <button type="button" disabled={busy} onClick={() => onDismiss(goal.id)}
          className="rounded border border-black/20 px-3 py-1 text-sm disabled:opacity-50 dark:border-white/30">
          {proposed ? "Dismiss" : "Stop"}
        </button>
        {!proposed && <span className="text-xs text-neutral-500">Its budget line ends at the next close.</span>}
      </div>
    </article>
  );
}
