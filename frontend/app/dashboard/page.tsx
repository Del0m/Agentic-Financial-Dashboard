"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import AccountsTable from "@/components/AccountsTable";
import BalanceChart from "@/components/BalanceChart";
import BudgetPie from "@/components/BudgetPie";
import DebtPlanCompare from "@/components/DebtPlanCompare";
import ForecastChart from "@/components/ForecastChart";
import DebtsTable from "@/components/DebtsTable";
import { goalTitle } from "@/components/GoalCard";
import NetWorthSummary from "@/components/NetWorthSummary";
import PlaidLinkButton from "@/components/PlaidLinkButton";
import RecurringList from "@/components/RecurringList";
import TransactionsTable from "@/components/TransactionsTable";
import ViewSwitch from "@/components/ViewSwitch";
import {
  correctTransaction, errorMessage, getAlerts, getBudget, getDashboard, getForecast, getGoals, getRecurring, getSim, getTransactions, setForecastBuffer, syncItem, type Budget,
  type Dashboard, type ExchangeResult, type Forecast, type Goal, type RecurringResponse, type SimState, type Txn,
} from "@/lib/api";
import { categoryLabel, formatCents, formatDate, formatMonth } from "@/lib/format";

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-black/10 p-4 dark:border-white/15">
      <h2 className="mb-3 font-semibold">{title}</h2>
      {children}
    </section>
  );
}

export default function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // "Account linked." after a link whose first sync worked; failed syncs come back from the
  // server as unsynced_items instead, so their Retry sync survives a page refresh
  const [linkedNotice, setLinkedNotice] = useState(false);
  const [syncError, setSyncError] = useState<{ itemId: number; message: string } | null>(null);
  const [retrying, setRetrying] = useState<number | null>(null);
  const [linkError, setLinkError] = useState<string | null>(null);
  // bumping this re-runs the fetch effect below
  const [reloadKey, setReloadKey] = useState(0);
  const load = useCallback(() => setReloadKey((k) => k + 1), []);
  // the budget agent (tpc-20): live pace bars, and how many alerts wait on the user. a failure here stays
  // inside the budget card and never blocks the rest of the dashboard
  const [budget, setBudget] = useState<Budget | null>(null);
  const [budgetError, setBudgetError] = useState<string | null>(null);
  const [pendingAlerts, setPendingAlerts] = useState(0);
  // the goal agent (tpc-25): accepted goals for the card, and how many proposals wait on the user. like the budget,
  // a failure stays inside its card
  const [goals, setGoals] = useState<Goal[] | null>(null);
  const [goalsError, setGoalsError] = useState<string | null>(null);
  // this month's transactions (tpc-22), with the line each counts against and a picker to correct it
  const [txns, setTxns] = useState<Txn[] | null>(null);
  const [txnError, setTxnError] = useState<string | null>(null);
  const [recurring, setRecurring] = useState<RecurringResponse | null>(null);
  const [recurringError, setRecurringError] = useState<string | null>(null);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [forecastError, setForecastError] = useState<string | null>(null);
  // a simulated (demo) user can't link a bank, so its Link button would only fail. null until known, so the button
  // doesn't request a link token first. without the admin routes (404) the user is a real one
  const [sim, setSim] = useState<SimState | null>(null);

  useEffect(() => {
    let cancelled = false;
    getSim()
      .then((s) => !cancelled && setSim(s))
      .catch(() => !cancelled && setSim({ simulated: false, profile: null, date: null }));
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  useEffect(() => {
    let cancelled = false;
    getBudget()
      .then((b) => {
        if (cancelled) return;
        setBudget(b);
        setBudgetError(null);
      })
      .catch((err) => !cancelled && setBudgetError(errorMessage(err)));
    getGoals("current")
      .then((g) => {
        if (cancelled) return;
        setGoals(g);
        setGoalsError(null);
      })
      .catch((err) => !cancelled && setGoalsError(errorMessage(err)));
    getAlerts("pending_user")
      .then((a) => !cancelled && setPendingAlerts(a.length))
      .catch(() => {}); // the link still works without its count
    getTransactions()
      .then((t) => {
        if (cancelled) return;
        setTxns(t);
        setTxnError(null);
      })
      .catch((err) => !cancelled && setTxnError(errorMessage(err)));
    getRecurring()
      .then((r) => {
        if (cancelled) return;
        setRecurring(r);
        setRecurringError(null);
      })
      .catch((err) => !cancelled && setRecurringError(errorMessage(err)));
    getForecast()
      .then((f) => {
        if (cancelled) return;
        setForecast(f); setForecastError(null);
      })
      .catch((err) => !cancelled && setForecastError(errorMessage(err)));
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  useEffect(() => {
    let cancelled = false; // drop a response that arrives after a newer fetch started
    getDashboard()
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setError(null);
      })
      .catch((err) => !cancelled && setError(errorMessage(err)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const syncing = data?.unsynced_items.some((item) => item.syncing) ?? false;
  useEffect(() => {
    if (!syncing) return;
    // Bound automatic polling; a stopped API must still leave a visible Retry sync action.
    const interval = setInterval(load, 2000);
    const timeout = setTimeout(() => clearInterval(interval), 120_000);
    return () => {
      clearInterval(interval);
      clearTimeout(timeout);
    };
  }, [syncing, load]);

  const onLinked = useCallback(
    (r: ExchangeResult) => {
      setLinkError(null);
      setSyncError(null);
      setLinkedNotice(r.synced || r.syncing);
      load();
    },
    [load],
  );

  const onLinkError = useCallback((message: string) => setLinkError(message), []);

  // a correction moves spending between lines, so the pace bars reload with it
  const onCorrect = useCallback(
    async (id: number, category: string, remember: boolean) => {
      const updated = await correctTransaction(id, category, remember);
      load();
      return updated;
    },
    [load],
  );

  async function retrySync(itemId: number) {
    setRetrying(itemId);
    setSyncError(null);
    try {
      await syncItem(itemId);
    } catch (err) {
      setSyncError({ itemId, message: errorMessage(err) });
    } finally {
      setRetrying(null);
      load(); // a partial sync may still have stored accounts
    }
  }

  const proposedGoals = goals?.filter((g) => g.status === "proposed").length ?? 0;
  const acceptedGoals = goals?.filter((g) => g.status === "accepted") ?? [];
  const linked = data !== null && data.accounts.length > 0;

  return (
    <main className="mx-auto w-full max-w-5xl space-y-6 p-4 sm:p-6">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <div className="flex flex-wrap items-center gap-3">
          <ViewSwitch />
          <Link href="/alerts" className="text-sm underline underline-offset-2">
            {pendingAlerts > 0 ? `Alerts (${pendingAlerts})` : "Alerts"}
          </Link>
          <Link href="/goals" className="text-sm underline underline-offset-2">
            {proposedGoals > 0 ? `Goals (${proposedGoals})` : "Goals"}
          </Link>
          <Link href="/chat" className="text-sm underline underline-offset-2">
            Chat
          </Link>
          {sim?.simulated && sim.date && (
            <span className="rounded-full border border-black/10 px-2.5 py-1 text-xs text-neutral-600 dark:border-white/15 dark:text-neutral-300">
              Demo · {formatDate(sim.date)}
            </span>
          )}
          {linked && sim?.simulated === false && <PlaidLinkButton label="Link another account" onLinked={onLinked} onError={onLinkError} />}
        </div>
      </header>

      {linkError && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {linkError}
        </p>
      )}

      {linkedNotice && (
        <div
          role="status"
          className="flex items-center justify-between gap-2 rounded-lg border border-black/10 p-3 text-sm dark:border-white/15"
        >
          <span>Account linked.{syncing ? " Syncing bank data…" : ""}</span>
          <button type="button" onClick={() => setLinkedNotice(false)} className="px-2 text-neutral-500" aria-label="Dismiss">
            ✕
          </button>
        </div>
      )}

      {data?.unsynced_items.map((item) => (
        <div
          key={item.id}
          role="status"
          className="flex flex-col gap-2 rounded-lg border border-amber-300 p-3 text-sm sm:flex-row sm:items-center sm:justify-between"
        >
          <span>
            {item.institution_name ?? "A bank"}{item.syncing
              ? " is linked. Bank data is syncing…"
              : " is linked, but loading its data failed."}
            {syncError?.itemId === item.id && (
              <span className="block text-red-600 dark:text-red-400">{syncError.message}</span>
            )}
          </span>
          <button
            type="button"
            onClick={() => retrySync(item.id)}
            disabled={retrying !== null}
            className="rounded-md border border-black/10 px-3 py-1.5 disabled:opacity-50 dark:border-white/15"
          >
            {retrying === item.id ? "Syncing…" : "Retry sync"}
          </button>
        </div>
      ))}

      {loading ? (
        <p className="text-neutral-500">Loading…</p>
      ) : error ? (
        <div role="alert" className="flex flex-col gap-3 rounded-lg border border-red-300 p-4 sm:flex-row sm:items-center sm:justify-between">
          <span className="text-red-700 dark:text-red-400">Couldn&apos;t load your dashboard: {error}</span>
          <button
            type="button"
            onClick={() => {
              setLoading(true);
              load();
            }}
            className="rounded-md border border-black/10 px-3 py-1.5 text-sm dark:border-white/15"
          >
            Retry
          </button>
        </div>
      ) : !linked ? (
        <section className="mx-auto max-w-md space-y-4 rounded-lg border border-black/10 p-6 text-center dark:border-white/15">
          <h2 className="text-lg font-semibold">Connect a bank</h2>
          <p className="text-sm text-neutral-500">
            Link an account through Plaid to see your balances, net worth and debts. Your bank login goes to Plaid,
            not to this app.
          </p>
          <PlaidLinkButton label="Connect a bank" onLinked={onLinked} onError={onLinkError} />
        </section>
      ) : (
        <>
          <NetWorthSummary data={data} />
          <Card title="Budget">
            {budgetError ? (
              <p className="text-sm text-red-600 dark:text-red-400">Couldn&apos;t load your budget: {budgetError}</p>
            ) : !budget ? (
              <p className="text-sm text-neutral-500">Loading…</p>
            ) : budget.period === null && budget.ended_period ? (
              <p className="text-sm text-neutral-500">
                Your budget ended on {formatDate(budget.ended_period.end)}. The next month opens when the period closes.
              </p>
            ) : budget.period === null ? (
              <p className="text-sm text-neutral-500">No budget yet. Run a budget check from Alerts.</p>
            ) : (
              <div className="space-y-3">
                <p className="text-xs text-neutral-500">
                  Day {budget.days_elapsed} of {budget.days_in_period}. The ring shows how your monthly budget is split,
                  and the list shows what you&apos;ve spent on each line so far.
                </p>
                <BudgetPie budget={budget} goals={acceptedGoals} />
                {budget.unbudgeted.length > 0 && (
                  <p className="text-sm text-neutral-500">
                    Unbudgeted:{" "}
                    {budget.unbudgeted.map((u) => `${categoryLabel(u.category)} ${formatCents(u.spent_cents)}`).join(", ")}
                  </p>
                )}
              </div>
            )}
          </Card>
          <Card title="Goals">
            {goalsError ? (
              <p className="text-sm text-red-600 dark:text-red-400">Couldn&apos;t load your goals: {goalsError}</p>
            ) : !goals ? (
              <p className="text-sm text-neutral-500">Loading…</p>
            ) : acceptedGoals.length === 0 ? (
              <p className="text-sm text-neutral-500">No active goals right now. Generate some from Goals.</p>
            ) : (
              <ul className="space-y-2">
                {acceptedGoals.map((g) => (
                  <li key={g.id} className="flex justify-between gap-3 text-sm">
                    <div>
                      <p>{goalTitle(g)}</p>
                      {g.start_date && <p className="text-xs text-neutral-500">From {formatMonth(g.start_date)}</p>}
                    </div>
                    <span className="tabular-nums">{formatCents(g.monthly_cents)}/mo</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Cash forecast">
            {forecastError ? <p className="text-sm text-red-600 dark:text-red-400">Couldn&apos;t load your cash forecast: {forecastError}</p>
              : !forecast ? <p className="text-sm text-neutral-500">Loading…</p>
              : <ForecastChart data={forecast} onSaveBuffer={async (cents) => { await setForecastBuffer(cents); load(); }} />}
          </Card>
          <Card title="Recurring charges">
            {recurringError ? (
              <p className="text-sm text-red-600 dark:text-red-400">Couldn&apos;t load your recurring charges: {recurringError}</p>
            ) : !recurring ? (
              <p className="text-sm text-neutral-500">Loading…</p>
            ) : <RecurringList data={recurring} />}
          </Card>
          <Card title="Transactions">
            {txnError ? (
              <p className="text-sm text-red-600 dark:text-red-400">Couldn&apos;t load your transactions: {txnError}</p>
            ) : !txns ? (
              <p className="text-sm text-neutral-500">Loading…</p>
            ) : (
              <TransactionsTable transactions={txns} lines={budget?.lines ?? []} onCorrect={onCorrect}
                onRemembered={load} />
            )}
          </Card>
          <Card title="Balances">
            <BalanceChart accounts={data.accounts} />
          </Card>
          <Card title="Debts by APR">
            <DebtsTable debts={data.debts} />
          </Card>
          {data.debts.length > 0 && (
            <Card title="Paying off your debts">
              <DebtPlanCompare suggestedExtraCents={forecast?.suggested_extra_cents} debtsKey={data.debts
                .map((d) => `${d.account_id}:${d.balance_cents}:${d.apr}:${d.min_payment_cents}`).join("|")} />
            </Card>
          )}
          <Card title="Accounts">
            <AccountsTable accounts={data.accounts} />
          </Card>
        </>
      )}
    </main>
  );
}
