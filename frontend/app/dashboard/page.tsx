"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import AccountsTable from "@/components/AccountsTable";
import BalanceChart from "@/components/BalanceChart";
import BudgetPaceBar from "@/components/BudgetPaceBar";
import DebtsTable from "@/components/DebtsTable";
import NetWorthSummary from "@/components/NetWorthSummary";
import PlaidLinkButton from "@/components/PlaidLinkButton";
import TransactionsTable from "@/components/TransactionsTable";
import {
  correctTransaction, errorMessage, getAlerts, getBudget, getDashboard, getTransactions, syncItem, type Budget,
  type Dashboard, type ExchangeResult, type Txn,
} from "@/lib/api";
import { categoryLabel, formatCents, formatDate } from "@/lib/format";

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
  // this month's transactions (tpc-22), with the line each counts against and a picker to correct it
  const [txns, setTxns] = useState<Txn[] | null>(null);
  const [txnError, setTxnError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getBudget()
      .then((b) => {
        if (cancelled) return;
        setBudget(b);
        setBudgetError(null);
      })
      .catch((err) => !cancelled && setBudgetError(errorMessage(err)));
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

  const onLinked = useCallback(
    (r: ExchangeResult) => {
      setLinkError(null);
      setSyncError(null);
      setLinkedNotice(r.synced);
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

  const linked = data !== null && data.accounts.length > 0;

  return (
    <main className="mx-auto w-full max-w-5xl space-y-6 p-4 sm:p-6">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <div className="flex items-center gap-3">
          <Link href="/alerts" className="text-sm underline underline-offset-2">
            {pendingAlerts > 0 ? `Alerts (${pendingAlerts})` : "Alerts"}
          </Link>
          <Link href="/chat" className="text-sm underline underline-offset-2">
            Chat
          </Link>
          {linked && <PlaidLinkButton label="Link another account" onLinked={onLinked} onError={onLinkError} />}
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
          <span>Account linked.</span>
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
            {item.institution_name ?? "A bank"} is linked, but loading its data failed.
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
                {budget.lines.map((line) => (
                  <BudgetPaceBar key={line.category} line={line} daysElapsed={budget.days_elapsed}
                    daysInPeriod={budget.days_in_period} />
                ))}
                {budget.unbudgeted.length > 0 && (
                  <p className="text-sm text-neutral-500">
                    Unbudgeted:{" "}
                    {budget.unbudgeted.map((u) => `${categoryLabel(u.category)} ${formatCents(u.spent_cents)}`).join(", ")}
                  </p>
                )}
              </div>
            )}
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
          <Card title="Accounts">
            <AccountsTable accounts={data.accounts} />
          </Card>
        </>
      )}
    </main>
  );
}
