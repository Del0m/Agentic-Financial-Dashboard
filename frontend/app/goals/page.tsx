"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import GoalCard from "@/components/GoalCard";
import {
  acceptGoal, dismissGoal, errorMessage, generateGoals, getGoalRun, getGoals, type Goal, type GoalRun,
} from "@/lib/api";

// a run can outlast the proxy's 30 s, so it runs in the background and the page asks how it's going. same values as
// the alerts page; a page file can't export them to share
const POLL_MS = 2000;
const MAX_POLLS = 300;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

// what a settled run says; failures also list the validator's reasons under it
type Result = { text: string; issues: string[] };

function resultOf(run: GoalRun): Result {
  const say = (text: string): Result => ({ text, issues: [] });
  switch (run.status) {
    case "expired":
      return say("A newer proposal replaced this one.");
    case "error": {
      const code = run.reason ?? run.error ?? "unknown error"; // an interrupted run has an error and no reason
      const issues = run.issues.map((i) => i.message);
      const stands = "Your earlier proposal stands.";
      switch (code) {
        case "goals_changed":
          return { text: "Your accepted goals changed while this ran, so nothing was written. Your earlier proposal stands. Generate again.", issues };
        case "incomplete_card_obligations": // a credit account with no balance, or a balance and no minimum payment
          return { text: `A credit card is missing its balance or minimum payment, so goals can't be worked out yet. ${stands}`, issues };
        case "validation_failed":
          return { text: `The model's proposal didn't fit after two tries. ${stands}`, issues };
        case "bad_model_output":
          return { text: `The model didn't return a usable proposal after two tries. ${stands}`, issues };
        default: // an exception name, or a run interrupted by a restart
          return { text: `The run couldn't finish. Check that the model is running, then try again. ${stands} (${code})`, issues };
      }
    }
    default:
      if (run.goal_ids.length) return say(`${plural(run.goal_ids.length, "goal")} proposed.`);
      switch (run.outcome) {
        case "no_surplus":
          return say("No money left over in a typical month, so there's nothing to put toward a goal.");
        case "nothing_offered":
          return say("Your preferences and accepted goals already cover every kind of goal on offer.");
        case "no_affordable_goals":
          return say("What's left over each month is under $1, too little for a goal.");
        default:
          return say("No new goals.");
      }
  }
}

export default function GoalsPage() {
  const [goals, setGoals] = useState<Goal[] | null>(null);
  // a failed load, and a failed click: apart, because the reload after a click would clear the message
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [deciding, setDeciding] = useState<number | null>(null);
  // bumping this re-runs the fetch effect below
  const [reloadKey, setReloadKey] = useState(0);
  const load = useCallback(() => setReloadKey((k) => k + 1), []);
  // false once the page is left, so an unfinished poll loop stops instead of running in the background
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false; // drop a response that arrives after a newer fetch started
    getGoals("current")
      .then((g) => {
        if (cancelled) return;
        setGoals(g);
        setLoadError(null);
      })
      .catch((err) => !cancelled && setLoadError(errorMessage(err)));
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  async function generate() {
    setGenerating(true);
    setResult(null);
    setActionError(null);
    try {
      const { run_id } = await generateGoals();
      if (run_id === null) {
        setResult({ text: "The run started but hasn't reported back yet. Refresh in a moment.", issues: [] });
        return;
      }
      let run: GoalRun | null = null;
      for (let i = 0; i < MAX_POLLS && mounted.current; i++) {
        await sleep(POLL_MS);
        if (!mounted.current) return;
        run = await getGoalRun(run_id);
        if (run.status !== "running") break;
      }
      if (!mounted.current) return;
      setResult(run && run.status !== "running" ? resultOf(run) : { text: "Still running. Check back later.", issues: [] });
    } catch (err) {
      setResult({ text: errorMessage(err), issues: [] });
    } finally {
      setGenerating(false);
      load();
    }
  }

  async function decide(id: number, accept: boolean) {
    setDeciding(id);
    setActionError(null);
    try {
      await (accept ? acceptGoal(id) : dismissGoal(id));
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setDeciding(null);
      load(); // also after a 409: the list drops the stale goal
    }
  }

  const proposed = goals?.filter((g) => g.status === "proposed") ?? [];
  const accepted = goals?.filter((g) => g.status === "accepted") ?? [];
  const card = (g: Goal) => (
    <GoalCard key={g.id} goal={g} busy={deciding !== null} onAccept={(id) => decide(id, true)}
      onDismiss={(id) => decide(id, false)} />
  );

  return (
    <main className="mx-auto w-full max-w-3xl space-y-6 p-4 sm:p-6">
      <header className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Goals</h1>
        <Link href="/dashboard" className="text-sm underline underline-offset-2">
          Dashboard
        </Link>
      </header>

      <section className="flex flex-col gap-3 rounded-lg border border-black/10 p-4 dark:border-white/15">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <button type="button" onClick={generate} disabled={generating}
            className="rounded-md bg-black px-3 py-1.5 text-sm text-white disabled:opacity-50 dark:bg-white dark:text-black">
            {generating ? "Generating…" : "Generate goals"}
          </button>
          {result && <p role="status" className="text-sm">{result.text}</p>}
        </div>
        {result && result.issues.length > 0 && (
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {result.issues.map((message, i) => <li key={i}>{message}</li>)}
          </ul>
        )}
      </section>

      {(loadError || actionError) && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {actionError ?? loadError}
        </p>
      )}

      {goals === null && !loadError ? (
        <p className="text-neutral-500">Loading…</p>
      ) : goals && goals.length === 0 ? (
        <p className="text-sm text-neutral-500">No goals right now. Generate goals to get a proposal.</p>
      ) : (
        <>
          {proposed.length > 0 && (
            <section className="space-y-3">
              <h2 className="font-semibold">Proposed</h2>
              {proposed.map(card)}
            </section>
          )}
          {accepted.length > 0 && (
            <section className="space-y-3">
              <h2 className="font-semibold">Accepted</h2>
              {accepted.map(card)}
            </section>
          )}
        </>
      )}
    </main>
  );
}
