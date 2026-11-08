"use client";

import { useEffect, useState } from "react";
import { errorMessage, getActivity, type ActivityRun, type TraceFlag, type TraceSignal } from "@/lib/api";
import { categoryLabel, formatCents, formatDate } from "@/lib/format";

// the budget agent at work: the steps of a run as they start, then what it decided for each flagged line and each
// signal, and who decided it (code, the model, memory, or you). a run in progress is polled; a finished one is read
// once per reload

const LIVE_POLL_MS = 800;

type Actor = "code" | "model" | "you";

// the budget graph's steps, in the order a full run takes them
export const PIPELINE: { id: string; label: string; actor: Actor }[] = [
  { id: "sync", label: "Sync bank", actor: "code" },
  { id: "load_state", label: "Load budget", actor: "code" },
  { id: "categorize", label: "Categorize", actor: "code" },
  { id: "update_totals", label: "Measure pace", actor: "code" },
  { id: "detect_recurring", label: "Find recurring", actor: "code" },
  { id: "update_graph", label: "Update knowledge graph", actor: "code" },
  { id: "forecast_cash", label: "Forecast cash", actor: "code" },
  { id: "detect_overspend", label: "Flag lines", actor: "code" },
  { id: "diagnose", label: "Diagnose cause", actor: "model" },
  { id: "decide_action", label: "Choose action", actor: "model" },
  { id: "decide_signals", label: "Decide on signals", actor: "model" },
  { id: "log_advice", label: "Write alerts and memory", actor: "code" },
  { id: "await_approval", label: "Wait for you", actor: "you" },
  { id: "persist_state", label: "Save run", actor: "code" },
];

const ACTIONS: Record<string, string> = {
  alert: "send an alert", suggest_cuts: "suggest cuts", adjust_next_budget: "propose next month's limit",
  no_op: "stay quiet", suggest_cancel: "suggest cancelling", delay_spend: "suggest delaying spending",
  move_from_savings: "suggest moving money from savings",
};
const CAUSES: Record<string, string> = {
  one_time: "One-time", recurring: "Recurring", seasonal: "Seasonal", reclassified: "Moved by a rule",
};
const SIGNALS: Record<string, string> = {
  low_balance: "Low balance ahead", new_subscription: "New subscription", price_increase: "Price increase",
  income_change: "Income change",
};
const OVERRIDES: Record<string, string> = {
  fallback: "The model didn't answer, so code wrote a plain alert instead.",
  downgraded: "Only a recurring cause may change next month's budget, and never this line, so code turned it into an alert.",
  escalated: "Staying quiet is only allowed once you've been told at this severity, so code sent an alert.",
  reclassified: "The spending only moved between lines, so code sent an alert instead of a change.",
  cut_dismissed: "You've dismissed this cut before, so code didn't propose it again.",
};
const ACTOR_STYLE: Record<Actor | "memory", string> = {
  code: "bg-neutral-200 text-neutral-800 dark:bg-neutral-700 dark:text-neutral-100",
  model: "bg-violet-600 text-white",
  memory: "bg-sky-600 text-white",
  you: "bg-amber-500 text-black",
};

const lineName = (category: string) => (category === "FREE" ? "Everything else" : categoryLabel(category));
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
const actionLabel = (action: string | null) => (action ? ACTIONS[action] ?? action : "—");

function Tag({ actor }: { actor: Actor | "memory" }) {
  return <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide ${ACTOR_STYLE[actor]}`}>{actor}</span>;
}

// steps a finished run reached, when this process didn't see it run (the cli, or a restart since)
function inferredSteps(run: ActivityRun): string[] {
  const flags = run.summary.trace?.flags ?? [];
  const skipped = new Set<string>();
  if (flags.length === 0) ["diagnose", "decide_action"].forEach((id) => skipped.add(id));
  if (run.status !== "awaiting_user" && !flags.some((f) => f.action === "adjust_next_budget")) skipped.add("await_approval");
  return PIPELINE.map((s) => s.id).filter((id) => !skipped.has(id));
}

function Pipeline({ run }: { run: ActivityRun }) {
  const live = run.status === "running";
  const reached = new Set(run.steps.length ? run.steps : run.status === "running" ? [] : inferredSteps(run));
  const current = live ? run.steps[run.steps.length - 1] : run.status === "awaiting_user" ? "await_approval" : null;
  return (
    <ol aria-label="Pipeline" className="flex flex-wrap gap-1.5">
      {PIPELINE.map((step) => {
        const isCurrent = step.id === current;
        const done = reached.has(step.id) && !isCurrent;
        const style = isCurrent
          ? `${ACTOR_STYLE[step.actor]} animate-pulse ring-2 ring-offset-1 ring-black/40 dark:ring-white/50`
          : done ? ACTOR_STYLE[step.actor] : "border border-dashed border-black/15 text-neutral-400 dark:border-white/20";
        return (
          <li key={step.id} aria-current={isCurrent ? "step" : undefined} data-state={isCurrent ? "current" : done ? "done" : "skipped"}
            className={`rounded-full px-2.5 py-1 text-xs ${style}`}>
            {step.label}
          </li>
        );
      })}
    </ol>
  );
}

function Step({ n, actor, title, children }: { n: number; actor: Actor | "memory"; title: string; children: React.ReactNode }) {
  return (
    <li className="grid grid-cols-[1.5rem_1fr] gap-2">
      <span className="mt-0.5 flex h-6 w-6 items-center justify-center rounded-full bg-black/5 text-xs dark:bg-white/10">{n}</span>
      <div className="space-y-1">
        <p className="flex flex-wrap items-center gap-2 text-sm font-medium">{title} <Tag actor={actor} /></p>
        <div className="text-sm text-neutral-700 dark:text-neutral-300">{children}</div>
      </div>
    </li>
  );
}

function measured(f: TraceFlag): string {
  if (f.spent_cents === undefined || f.limit_cents === undefined) return f.reason === "over_limit" ? "Over its limit." : "Ahead of pace.";
  const when = f.days_elapsed && f.days_in_period ? ` by day ${f.days_elapsed} of ${f.days_in_period}` : "";
  const how = f.reason === "over_limit"
    ? `over its limit by ${formatCents(f.spent_cents - f.limit_cents)}`
    : `${(f.pace ?? 0).toFixed(2)}× the pace that would just fit the month`;
  return `${formatCents(f.spent_cents)} of ${formatCents(f.limit_cents)}${when}: ${how}.`;
}

export function FlagTrace({ flag }: { flag: TraceFlag }) {
  const earlier = flag.earlier_alerts ?? [];
  const past = flag.past_advice ?? [];
  const quiet = flag.alert_id === null;
  return (
    <article className="space-y-3 rounded-lg border border-black/10 p-3 dark:border-white/15">
      <h4 className="font-semibold">
        {lineName(flag.category)} <span className="text-sm font-normal text-neutral-500">· {flag.reason === "over_limit" ? "over limit" : "ahead of pace"}</span>
      </h4>
      <ol className="space-y-3">
        <Step n={1} actor="code" title="Measured">
          {measured(flag)}
          {flag.largest_cents != null && (flag.largest_share ?? 0) >= 0.5 && (
            <> The largest purchase, {formatCents(flag.largest_cents)}, is {Math.round((flag.largest_share ?? 0) * 100)}% of it.</>
          )}
          {flag.projected_cents !== undefined && flag.reason === "pace" && <> At this rate the month ends near {formatCents(flag.projected_cents)}.</>}
        </Step>
        <Step n={2} actor="memory" title="Remembered">
          {earlier.length
            ? `Already alerted you ${earlier.length === 1 ? "once" : `${earlier.length} times`} about this line this month (latest: ${earlier[earlier.length - 1].severity}, ${earlier[earlier.length - 1].status.replace("_", " ")}).`
            : "No alert about this line yet this month."}
          {past.length > 0 && <> Advice so far: {past.map((p) => `${p.month}: ${actionLabel(p.action)}${p.outcome ? `, ${p.outcome}` : ""}`).join("; ")}.</>}
          {flag.graph_facts && <> Plus facts from the knowledge graph.</>}
        </Step>
        <Step n={3} actor="model" title="Diagnosed">
          {flag.cause ? (
            <><strong>{CAUSES[flag.cause] ?? flag.cause}</strong>{flag.evidence && <>: <q>{flag.evidence}</q></>}</>
          ) : "The model gave no diagnosis."}
        </Step>
        <Step n={4} actor="model" title="Decided">
          {flag.model_action ? <>Chose to <strong>{actionLabel(flag.model_action)}</strong>.</> : "No answer from the model."}
        </Step>
        <Step n={5} actor="code" title="Checked">
          {flag.override ? OVERRIDES[flag.override] ?? flag.override : "Within the rules, so it stands."}
        </Step>
      </ol>
      <p className={`text-sm font-medium ${quiet ? "text-neutral-500" : ""}`}>
        {quiet ? "Result: stayed quiet, no new alert." : `Result: wrote alert #${flag.alert_id}` +
          (flag.action === "adjust_next_budget" && flag.proposed_limit_cents !== null
            ? `, proposing ${formatCents(flag.proposed_limit_cents)} for next month, and paused for your decision.` : ".")}
      </p>
    </article>
  );
}

function signalFacts(s: TraceSignal): string {
  if (s.kind === "low_balance" && s.shortfall_cents !== null) {
    return `the forecast comes up ${formatCents(s.shortfall_cents)} short` + (s.days_ahead !== null ? ` in ${plural(s.days_ahead, "day")}` : "");
  }
  if (s.typical_cents !== null) return `${formatCents(s.typical_cents)}${s.frequency ? ` ${s.frequency.replace("_", "-")}` : ""}`;
  return s.category ? lineName(s.category) : "";
}

function SignalRow({ signal }: { signal: TraceSignal }) {
  return (
    <li className="flex flex-wrap items-center gap-2 text-sm">
      <span className="font-medium">{SIGNALS[signal.kind] ?? signal.kind}</span>
      <span className="text-neutral-600 dark:text-neutral-400">{signalFacts(signal)}</span>
      <span>→</span>
      {signal.fallback ? <span>no model answer, code sent an alert</span> : <span>model chose to {actionLabel(signal.model_action)}</span>}
      <Tag actor={signal.fallback ? "code" : "model"} />
      <span className="text-neutral-500">{signal.alert_id !== null ? `alert #${signal.alert_id}` : "already told you"}</span>
    </li>
  );
}

function working(step: string | undefined): string {
  const s = PIPELINE.find((p) => p.id === step);
  if (!s) return "The agent is working…";
  const who = s.actor === "model" ? "The model is on" : s.actor === "you" ? "Waiting for you:" : "Code is on";
  return `${who} ${s.label.toLowerCase()}…`;
}

export function RunView({ run }: { run: ActivityRun }) {
  const trace = run.summary.trace;
  const live = run.status === "running";
  return (
    <div className="space-y-4">
      <Pipeline run={run} />
      {live ? (
        <p role="status" className="text-sm">{working(run.steps[run.steps.length - 1])}</p>
      ) : run.status === "error" ? (
        <p className="text-sm text-red-600 dark:text-red-400">The run failed ({run.summary.error ?? "error"}).</p>
      ) : !trace ? (
        <p className="text-sm text-neutral-500">This run finished before decision traces were recorded.</p>
      ) : (
        <>
          {trace.flags.length === 0 ? (
            <p className="text-sm">Checked {plural(trace.lines_checked, "budget line")}: nothing is ahead of pace, so the model wasn&apos;t asked.</p>
          ) : (
            <div className="space-y-3">{trace.flags.map((f) => <FlagTrace key={f.category} flag={f} />)}</div>
          )}
          {trace.signals.length > 0 && (
            <div className="space-y-1">
              <h4 className="text-sm font-semibold">Signals from recurring charges and the cash forecast</h4>
              <ul className="space-y-1">{trace.signals.map((s, i) => <SignalRow key={i} signal={s} />)}</ul>
            </div>
          )}
          <p className="text-xs text-neutral-500">
            {plural(run.summary.llm_calls ?? 0, "model call")}
            {(run.summary.llm_failures ?? 0) > 0 && `, ${run.summary.llm_failures} without an answer`}
            {run.summary.streams && ` · ${plural(run.summary.streams.count, "recurring charge")} tracked`}
          </p>
        </>
      )}
    </div>
  );
}

function runLine(run: ActivityRun): string {
  if (run.status === "running") return "running";
  const flags = run.summary.flags?.length ?? run.summary.trace?.flags.length ?? 0;
  const written = run.summary.alert_ids?.length ?? 0;
  return `${plural(flags, "flag")}, ${written ? plural(written, "new alert") : "no new alerts"}`;
}

// the panel on the Alerts page. `live` is true while the page has a run or a simulation going
export default function AgentActivity({ live, reloadKey }: { live: boolean; reloadKey: number }) {
  const [runs, setRuns] = useState<ActivityRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = () => {
      getActivity(6)
        .then((r) => {
          if (cancelled) return;
          setRuns(r);
          setError(null);
        })
        .catch((err) => !cancelled && setError(errorMessage(err)))
        .finally(() => {
          if (!cancelled && live) timer = setTimeout(tick, LIVE_POLL_MS);
        });
    };
    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [live, reloadKey]);

  // while the agent works, follow the newest run; otherwise show the one picked, or the newest
  const shown = runs?.length ? (live ? runs[0] : runs.find((r) => r.run_id === picked) ?? runs[0]) : null;

  return (
    <section aria-label="Agent activity" className="space-y-3 rounded-lg border border-black/10 p-4 dark:border-white/15">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-semibold">Agent activity</h2>
        {shown && (
          <span className="text-xs text-neutral-500">
            Run #{shown.run_id}{shown.as_of && ` · ${formatDate(shown.as_of)}`}
          </span>
        )}
      </header>
      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
      {runs === null && !error ? (
        <p className="text-sm text-neutral-500">Loading…</p>
      ) : !shown ? (
        <p className="text-sm text-neutral-500">No budget runs yet. Run a budget check to watch the agent work.</p>
      ) : (
        <>
          <RunView run={shown} />
          {runs && runs.length > 1 && (
            <div className="flex flex-wrap gap-1.5 border-t border-black/10 pt-3 dark:border-white/15">
              <span className="text-xs text-neutral-500">Earlier runs:</span>
              {runs.map((r) => (
                <button key={r.run_id} type="button" onClick={() => setPicked(r.run_id)} disabled={live}
                  aria-pressed={r.run_id === shown.run_id}
                  className={`rounded border px-2 py-0.5 text-xs disabled:opacity-50 ${r.run_id === shown.run_id
                    ? "border-black dark:border-white" : "border-black/10 dark:border-white/15"}`}>
                  {r.as_of ? formatDate(r.as_of) : `#${r.run_id}`} · {runLine(r)}
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
