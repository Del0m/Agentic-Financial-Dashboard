import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";
import { FlagTrace, RunView, syncLine } from "@/components/AgentActivity";
import type { ActivityRun, TraceFlag } from "@/lib/api";

afterEach(cleanup);

function flag(over: Partial<TraceFlag> = {}): TraceFlag {
  return { category: "FREE", reason: "pace", spent_cents: 120000, limit_cents: 215800, pace: 1.44, days_elapsed: 12,
           days_in_period: 31, projected_cents: 310000, largest_cents: 120000, largest_share: 1, earlier_alerts: [],
           past_advice: [], graph_facts: false, cause: "one_time", evidence: "One large ElectroHub purchase.",
           model_action: "alert", action: "alert", override: null, proposed_limit_cents: null, alert_id: 8, ...over };
}

function run(over: Partial<ActivityRun> = {}): ActivityRun {
  return { run_id: 90, status: "ok", started_at: null, finished_at: null, as_of: "2026-10-12", steps: [],
           summary: { trace: { lines_checked: 4, flags: [flag()], signals: [] }, llm_calls: 2, llm_failures: 0 },
           ...over };
}

describe("FlagTrace", () => {
  test("walks from measurement through the model's diagnosis to the alert written", () => {
    render(<FlagTrace flag={flag()} />);
    expect(screen.getByText("Everything else")).toBeTruthy();
    expect(screen.getByText(/\$1,200\.00 of \$2,158\.00 by day 12 of 31: 1\.44× the pace/)).toBeTruthy();
    expect(screen.getByText("No alert about this line yet this month.")).toBeTruthy();
    expect(screen.getByText("One-time")).toBeTruthy();
    expect(screen.getByText("One large ElectroHub purchase.")).toBeTruthy();
    expect(screen.getByText("Within the rules, so it stands.")).toBeTruthy();
    expect(screen.getByText("Result: wrote alert #8.")).toBeTruthy();
  });

  test("shows memory leading the model to stay quiet", () => {
    render(<FlagTrace flag={flag({ model_action: "no_op", action: "no_op", alert_id: null,
      earlier_alerts: [{ severity: "medium", action: "alert", status: "pending_user" }] })} />);
    expect(screen.getByText(/Already alerted you once about this line this month/)).toBeTruthy();
    expect(screen.getByText("stay quiet")).toBeTruthy();
    expect(screen.getByText("Result: stayed quiet, no new alert.")).toBeTruthy();
  });

  test("shows when code overruled the model", () => {
    render(<FlagTrace flag={flag({ model_action: "adjust_next_budget", override: "downgraded" })} />);
    expect(screen.getByText("propose next month's limit")).toBeTruthy();
    expect(screen.getByText(/Only a recurring cause may change next month's budget/)).toBeTruthy();
  });

  test("says so when the model didn't answer", () => {
    render(<FlagTrace flag={flag({ cause: null, evidence: null, model_action: null, override: "fallback" })} />);
    expect(screen.getByText("The model gave no diagnosis.")).toBeTruthy();
    expect(screen.getByText(/The model didn't answer, so code wrote a plain alert/)).toBeTruthy();
  });

  test("a proposal says it paused for the user's decision", () => {
    render(<FlagTrace flag={flag({ category: "PERSONAL_CARE", reason: "over_limit", cause: "recurring",
      model_action: "adjust_next_budget", action: "adjust_next_budget", proposed_limit_cents: 13500 })} />);
    expect(screen.getByText(/proposing \$135\.00 for next month, and paused for your decision/)).toBeTruthy();
  });
});

describe("RunView", () => {
  test("a running run highlights the step it is on", () => {
    render(<RunView run={run({ status: "running", steps: ["sync", "load_state", "categorize", "update_totals",
      "detect_recurring", "update_graph", "forecast_cash", "detect_overspend", "diagnose"] })} />);
    const current = screen.getByText("Diagnose cause");
    expect(current.getAttribute("data-state")).toBe("current");
    expect(screen.getByText("Measure pace").getAttribute("data-state")).toBe("done");
    expect(screen.getByText("Save run").getAttribute("data-state")).toBe("skipped");
    expect(screen.getByText("The model is on diagnose cause…")).toBeTruthy();
  });

  test("a quiet day says the model wasn't asked, and skips the model steps", () => {
    render(<RunView run={run({ summary: { trace: { lines_checked: 4, flags: [], signals: [] }, llm_calls: 0 } })} />);
    expect(screen.getByText(/Checked 4 budget lines: nothing is ahead of pace/)).toBeTruthy();
    expect(screen.getByText("Diagnose cause").getAttribute("data-state")).toBe("skipped");
  });

  test("lists signals with the model's choice", () => {
    render(<RunView run={run({ summary: { trace: { lines_checked: 4, flags: [], signals: [
      { kind: "new_subscription", category: "ENTERTAINMENT", shortfall_cents: null, days_ahead: null,
        typical_cents: 1549, frequency: "monthly", model_action: "alert", action: "alert", fallback: false, alert_id: 10 },
    ] } } })} />);
    expect(screen.getByText("New subscription")).toBeTruthy();
    expect(screen.getByText("$15.49 monthly")).toBeTruthy();
    expect(screen.getByText("model chose to send an alert")).toBeTruthy();
    expect(screen.getByText("alert #10")).toBeTruthy();
  });

  test("an older run without a trace says so", () => {
    render(<RunView run={run({ summary: {} })} />);
    expect(screen.getByText(/finished before decision traces were recorded/)).toBeTruthy();
  });
});

describe("syncLine", () => {
  test("a plaid user's run says what the bank feed brought in", () => {
    expect(syncLine({ items: 1, errors: [], added: 1 })).toBe("Bank sync through Plaid: 1 new transaction from 1 linked bank.");
  });

  test("a bank that couldn't be reached is called out", () => {
    expect(syncLine({ items: 4, errors: ["PlaidError", "PlaidError", "PlaidError"], added: 0 }))
      .toBe("Bank sync through Plaid: 0 new transactions from 4 linked banks; 3 of them couldn't be reached, so their data may be stale.");
  });

  test("the demo user's skipped sync shows no line", () => {
    expect(syncLine({ skipped: true })).toBeNull();
    expect(syncLine(null)).toBeNull();
  });

  test("a finished run shows the sync line", () => {
    render(<RunView run={run({ summary: { ...run().summary, sync: { items: 1, errors: [], added: 2 } } })} />);
    expect(screen.getByText("Bank sync through Plaid: 2 new transactions from 1 linked bank.")).toBeTruthy();
  });
});
