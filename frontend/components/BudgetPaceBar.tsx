import type { BudgetLine } from "@/lib/api";
import { formatCents, lineLabel } from "@/lib/format";

// one budget line: how much of the limit is spent, against how much of the month has passed (the marker).
// amber when spending is ahead of pace, red once it's over the limit. the words say the same thing, so the colour
// is never the only signal
export default function BudgetPaceBar({ line, daysElapsed, daysInPeriod }: {
  line: BudgetLine;
  daysElapsed: number;
  daysInPeriod: number;
}) {
  const label = lineLabel(line);
  // an accepted goal is a planned amount, not spending: nothing reliably measures what was paid toward it
  if (line.group === "goal") {
    return (
      <div className="flex justify-between gap-2 text-sm">
        <span>{label}</span>
        <span className="tabular-nums">{formatCents(line.limit_cents)}/mo</span>
      </div>
    );
  }
  const share = line.limit_cents > 0 ? Math.min(line.spent_cents / line.limit_cents, 1) : 0;
  const percent = Math.round(share * 100);
  const today = (daysElapsed / daysInPeriod) * 100;
  const color = line.reason === "over_limit" ? "bg-red-600" : line.reason === "pace" ? "bg-amber-500" : "bg-neutral-500";
  const left = line.limit_cents - line.spent_cents;
  // fixed lines are bills paid once, so "left" and pace mean nothing there; saving can't be overspent
  const status = line.kind === "fixed" || line.group === "saving" ? null
    : left < 0 ? { text: `${formatCents(-left)} over`, className: "text-red-600 dark:text-red-400" }
    : line.reason === "pace" ? { text: `${formatCents(left)} left · spending fast`, className: "text-amber-700 dark:text-amber-400" }
    : { text: `${formatCents(left)} left`, className: "text-neutral-500" };
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap justify-between gap-x-2 text-sm">
        <span>
          {label}
          {line.kind === "fixed" && <span className="text-neutral-500"> (fixed)</span>}
        </span>
        <span className="tabular-nums">
          {formatCents(line.spent_cents)} of {formatCents(line.limit_cents)}
          {status && <span className={status.className}> · {status.text}</span>}
        </span>
      </div>
      <div
        role="meter"
        aria-label={`${label} spent`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        className="relative h-2 rounded bg-black/10 dark:bg-white/15"
      >
        <div data-testid="fill" className={`h-2 rounded ${color}`} style={{ width: `${percent}%` }} />
        <div data-testid="today" className="absolute -top-1 h-4 w-px bg-black/60 dark:bg-white/70" style={{ left: `${today}%` }} />
      </div>
    </div>
  );
}
