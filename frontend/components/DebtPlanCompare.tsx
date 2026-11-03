"use client";

import { useEffect, useRef, useState } from "react";
import { debtPlan, errorMessage, type DebtPlan, type Plan, type PlanExcluded } from "@/lib/api";
import { formatCents } from "@/lib/format";

// avalanche vs snowball for the user's debts (tpc-32). the backend simulates; this only shows the numbers.
// typing in the extra waits 300 ms, and only the latest request's answer is shown

const DEBOUNCE_MS = 300;
const REASONS: Record<PlanExcluded["reason"], string> = {
  no_apr: "no APR on file",
  no_minimum: "no minimum payment on file",
  mortgage: "mortgage (left out)",
};

const monthYear = new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric", timeZone: "UTC" });

function Column({ plan, base, extraCents, cheapest }: {
  plan: Plan; base: Plan; extraCents: number; cheapest: boolean;
}) {
  const title = plan.strategy === "avalanche" ? "Avalanche" : "Snowball";
  return (
    <section className="flex-1 space-y-2 rounded border border-black/10 p-3 dark:border-white/15">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="font-medium">{title}</h3>
        {cheapest && <span className="text-xs text-green-700 dark:text-green-400">saves the most interest</span>}
      </div>
      <p className="text-xs text-neutral-500">
        {plan.strategy === "avalanche" ? "Highest APR first" : "Smallest balance first"}
      </p>
      {plan.months === null ? (
        plan.never.map((d) => (
          <p key={d.account_id} className="text-sm text-red-600 dark:text-red-400">
            {d.name} isn&apos;t paid off within 50 years at this pace.
          </p>
        ))
      ) : (
        <>
          <p className="text-lg tabular-nums">{plan.months} months</p>
          {plan.debt_free && <p className="text-sm">Debt-free {monthYear.format(new Date(plan.debt_free))}</p>}
          <p className="text-sm">
            Total interest <span className="tabular-nums">{formatCents(plan.total_interest_cents)}</span>
          </p>
          <ol className="list-decimal space-y-0.5 pl-5 text-sm">
            {plan.order.map((p) => (
              <li key={p.account_id}>{p.name}: {monthYear.format(new Date(p.date))}</li>
            ))}
          </ol>
          {extraCents > 0 && base.months !== null && (
            <p className="text-sm text-neutral-600 dark:text-neutral-300">
              +{formatCents(extraCents).replace(/\.00$/, "")}/mo: {base.months - plan.months} months sooner,{" "}
              {formatCents((base.total_interest_cents ?? 0) - (plan.total_interest_cents ?? 0))} less interest
            </p>
          )}
        </>
      )}
    </section>
  );
}

// debtsKey changes when the dashboard's debts do (a new link, a sync), so the plan is asked for again
export default function DebtPlanCompare({ debtsKey = "", fetchPlan = debtPlan, suggestedExtraCents }: {
  debtsKey?: string; fetchPlan?: typeof debtPlan; suggestedExtraCents?: number | null;
}) {
  const [dollars, setDollars] = useState("0");
  const [result, setResult] = useState<{ extraCents: number; plan: DebtPlan } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const latest = useRef(0);

  useEffect(() => {
    const extraCents = Math.max(0, Math.round(Number(dollars) || 0)) * 100;
    const timer = setTimeout(() => {
      const id = ++latest.current;
      fetchPlan(extraCents).then(
        (plan) => { if (id === latest.current) { setResult({ extraCents, plan }); setError(null); } },
        (err) => { if (id === latest.current) setError(errorMessage(err)); },
      );
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [dollars, debtsKey, fetchPlan]);

  const plan = result?.plan;
  const planned = plan && !(plan.avalanche.months === 0 && plan.avalanche.order.length === 0);
  // a never plan has no total, so it's never the cheaper one
  const a = plan?.avalanche.total_interest_cents ?? Infinity;
  const s = plan?.snowball.total_interest_cents ?? Infinity;

  return (
    <div className="space-y-3">
      <label className="flex items-center gap-2 text-sm">
        <span>Extra per month</span>
        <span className="text-neutral-500">$</span>
        <input type="number" min={0} step={1} inputMode="numeric" value={dollars} aria-label="Extra per month"
          onChange={(e) => setDollars(e.target.value)}
          className="w-28 rounded border border-black/20 bg-transparent px-2 py-1 dark:border-white/25" />
      </label>
      {suggestedExtraCents != null && <p className="text-sm text-neutral-500">You could add about {formatCents(suggestedExtraCents)}.</p>}
      {error && <p className="text-sm text-red-600 dark:text-red-400">Couldn&apos;t plan your payoff: {error}</p>}
      {!plan ? (
        !error && <p className="text-sm text-neutral-500">Loading…</p>
      ) : (
        <>
          {planned && (
            <div className="flex flex-col gap-3 sm:flex-row">
              <Column plan={plan.avalanche} base={plan.baseline.avalanche} extraCents={result.extraCents}
                cheapest={a < s} />
              <Column plan={plan.snowball} base={plan.baseline.snowball} extraCents={result.extraCents}
                cheapest={s < a} />
            </div>
          )}
          {plan.excluded.length > 0 && (
            <div className="text-sm">
              <p className="text-neutral-500">Left out of the plan:</p>
              <ul className="list-disc pl-5">
                {plan.excluded.map((e) => <li key={e.account_id}>{e.name}: {REASONS[e.reason]}</li>)}
              </ul>
            </div>
          )}
          <p className="text-xs text-neutral-500">
            Interest is charged monthly at APR/12, and minimum payments are held at today&apos;s amounts. Not
            financial advice.
          </p>
        </>
      )}
    </div>
  );
}
