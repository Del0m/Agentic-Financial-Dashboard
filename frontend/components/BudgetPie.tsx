"use client";

import { useState } from "react";
import { goalTitle } from "@/components/GoalCard";
import type { Budget, BudgetLine, Goal } from "@/lib/api";
import { formatCents, formatMonth, lineLabel } from "@/lib/format";

// seven categorical slots (globals.css), given out in line order so a line keeps its colour as spending changes.
// past seven, the smallest lines share the grey "other" slice. red is left out: it means "over" in the text
const SLOTS = 7;
const R = 75; // ring radius in the 200x200 box
const C = 2 * Math.PI * R;
const GAP = 2; // surface gap between slices

type Slice = { key: string; label: string; cents: number; color: string; lines: BudgetLine[] };

// what the user reads beside the amounts. fixed lines are bills paid once, so "left" means nothing there, and
// saving can't be overspent
export function lineStatus(line: BudgetLine): { text: string; className: string } | null {
  if (line.kind === "fixed" || line.group === "saving" || line.group === "goal") return null;
  const left = line.limit_cents - line.spent_cents;
  if (left < 0) return { text: `${formatCents(-left)} over`, className: "text-red-600 dark:text-red-400" };
  if (line.reason === "pace") return { text: `${formatCents(left)} left · spending fast`, className: "text-amber-700 dark:text-amber-400" };
  return { text: `${formatCents(left)} left`, className: "text-neutral-500" };
}

export function slicesOf(lines: BudgetLine[]): Slice[] {
  const sized = lines.filter((l) => l.limit_cents > 0);
  const named = sized.length > SLOTS ? [...sized].sort((a, b) => b.limit_cents - a.limit_cents).slice(0, SLOTS - 1) : sized;
  const slices: Slice[] = [];
  const rest: BudgetLine[] = [];
  for (const l of sized) {
    if (named.includes(l)) {
      slices.push({ key: l.category, label: lineLabel(l), cents: l.limit_cents, color: `var(--slice-${slices.length + 1})`, lines: [l] });
    } else rest.push(l);
  }
  if (rest.length) {
    slices.push({ key: "other", label: "Other lines", cents: rest.reduce((s, l) => s + l.limit_cents, 0),
                  color: "var(--slice-other)", lines: rest });
  }
  return slices;
}

const pct = (part: number, whole: number) => (whole > 0 ? Math.round((part / whole) * 100) : 0);

// the budget as a ring: each slice is one line's share of the month's plan. the list beside it says what has been
// spent on each line, so the over / spending-fast state is always in words, never colour alone
// an accepted goal joins the budget on its start date, at the close before it, so until then it's listed below the ring
export function upcomingGoals(budget: Budget, goals: Goal[]): Goal[] {
  const end = budget.period?.end;
  return end ? goals.filter((g) => g.status === "accepted" && g.start_date !== null && g.start_date > end) : [];
}

export default function BudgetPie({ budget, goals = [] }: { budget: Budget; goals?: Goal[] }) {
  const [active, setActive] = useState<string | null>(null);
  const slices = slicesOf(budget.lines);
  const planned = slices.reduce((s, x) => s + x.cents, 0);
  const spent = budget.lines.filter((l) => l.group !== "goal").reduce((s, l) => s + l.spent_cents, 0);
  const sliceOf = (l: BudgetLine) => slices.find((s) => s.lines.includes(l));
  const focus = slices.find((s) => s.key === active) ?? null;
  const upcoming = upcomingGoals(budget, goals);
  const nextLimits = budget.next_start ? budget.next_limits ?? [] : [];
  const aria = `Budget split, ${formatCents(planned)} a month: `
    + slices.map((s) => `${s.label} ${formatCents(s.cents)} (${pct(s.cents, planned)}%)`).join(", ") + ".";

  // where each slice starts along the ring
  const offsets = slices.map((_, i) => slices.slice(0, i).reduce((sum, x) => sum + (x.cents / planned) * C, 0));
  return (
    <div className="space-y-3">
    <div className="flex flex-col items-center gap-4 sm:flex-row sm:items-start">
      <svg viewBox="0 0 200 200" role="img" aria-label={aria} className="w-44 shrink-0">
        <g transform="rotate(-90 100 100)">
          {slices.length === 0 && <circle cx={100} cy={100} r={R} fill="none" strokeWidth={30} className="stroke-black/10 dark:stroke-white/15" />}
          {slices.map((s, i) => {
            const len = (s.cents / planned) * C;
            const offset = offsets[i];
            const drawn = slices.length > 1 ? Math.max(len - GAP, 1) : len;
            return (
              <circle key={s.key} data-testid="slice" data-key={s.key} cx={100} cy={100} r={R} fill="none"
                strokeWidth={active === s.key ? 34 : 30} strokeDasharray={`${drawn} ${C - drawn}`} strokeDashoffset={-offset}
                style={{ stroke: s.color, opacity: active && active !== s.key ? 0.35 : 1, transition: "opacity 120ms, stroke-width 120ms" }}
                onMouseEnter={() => setActive(s.key)} onMouseLeave={() => setActive(null)}>
                <title>{`${s.label}: ${formatCents(s.cents)} a month (${pct(s.cents, planned)}%)`}</title>
              </circle>
            );
          })}
        </g>
        {focus ? (
          <text x={100} y={100} textAnchor="middle" className="fill-current">
            <tspan x={100} dy={-8} fontSize={13} fontWeight={600}>{focus.label}</tspan>
            <tspan x={100} dy={18} fontSize={12}>{formatCents(focus.cents)} · {pct(focus.cents, planned)}%</tspan>
          </text>
        ) : (
          <text x={100} y={100} textAnchor="middle" className="fill-current">
            <tspan x={100} dy={-8} fontSize={16} fontWeight={600}>{formatCents(spent)}</tspan>
            <tspan x={100} dy={18} fontSize={11} className="fill-neutral-500">spent of {formatCents(planned)}</tspan>
          </text>
        )}
      </svg>
      <ul className="w-full min-w-0 space-y-2 text-sm sm:max-w-lg">
        {budget.lines.map((l) => {
          const slice = sliceOf(l);
          const status = lineStatus(l);
          return (
            <li key={l.category} tabIndex={slice ? 0 : undefined}
              onMouseEnter={() => slice && setActive(slice.key)} onMouseLeave={() => setActive(null)}
              onFocus={() => slice && setActive(slice.key)} onBlur={() => setActive(null)}
              className={`flex flex-wrap items-baseline justify-between gap-x-2 rounded px-1 outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${active && slice?.key === active ? "bg-black/5 dark:bg-white/10" : ""}`}>
              <span className="flex items-center gap-2">
                <span aria-hidden data-testid="swatch" className="inline-block h-3 w-3 shrink-0 rounded-sm border border-black/10 dark:border-white/15"
                  style={{ background: slice?.color ?? "transparent" }} />
                {lineLabel(l)}
                {l.kind === "fixed" && l.group !== "goal" && <span className="text-neutral-500">(fixed)</span>}
              </span>
              <span className="ml-auto text-right tabular-nums">
                {l.group === "goal" ? `${formatCents(l.limit_cents)}/mo planned`
                  : `${formatCents(l.spent_cents)} of ${formatCents(l.limit_cents)}`}
                {status && <span className={status.className}> · {status.text}</span>}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
      {(nextLimits.length > 0 || upcoming.length > 0) && (
        <div className="min-w-0 space-y-2 border-t border-black/10 pt-2 text-sm sm:ml-48 sm:max-w-lg dark:border-white/15">
          {nextLimits.length > 0 && (
            <div>
              <p className="text-xs text-neutral-500">Changes you approved, starting {formatMonth(budget.next_start!)}:</p>
              <ul className="mt-1 space-y-1">
                {nextLimits.map((n) => (
                  <li key={n.category} className="flex flex-wrap items-baseline justify-between gap-x-2 px-1">
                    <span>{lineLabel(n)}</span>
                    <span className="ml-auto text-right tabular-nums text-neutral-500">
                      {formatCents(n.limit_cents)} now → {formatCents(n.next_cents)} next month
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {upcoming.length > 0 && (
            <div>
              <p className="text-xs text-neutral-500">Accepted goals join your budget when their month starts:</p>
              <ul className="mt-1 space-y-1">
                {upcoming.map((g) => (
                  <li key={g.id} className="flex flex-wrap items-baseline justify-between gap-x-2 px-1">
                    <span className="flex items-center gap-2">
                      <span aria-hidden className="inline-block h-3 w-3 shrink-0 rounded-sm border border-dashed border-neutral-400" />
                      {goalTitle(g)}
                    </span>
                    <span className="ml-auto text-right tabular-nums text-neutral-500">
                      {formatCents(g.monthly_cents)}/mo from {formatMonth(g.start_date!)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
