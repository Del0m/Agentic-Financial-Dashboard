import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import AlertCard from "@/components/AlertCard";
import type { Alert } from "@/lib/api";

afterEach(cleanup);

function alert(over: Partial<Alert> = {}): Alert {
  return { id: 3, kind: "overspend", category: "GENERAL_MERCHANDISE", severity: "high", cause: "one_time",
           action: "alert", message: "A $600 purchase put you over.", proposed_limit_cents: null,
           status: "pending_user", created_at: "2026-10-20T12:00:00Z", period_start: "2026-10-01",
           awaiting_approval: false, stream_id: null, ...over };
}

function draw(a: Alert, busy = false) {
  const onAccept = vi.fn(), onDismiss = vi.fn();
  render(<AlertCard alert={a} onAccept={onAccept} onDismiss={onDismiss} busy={busy} />);
  return { onAccept, onDismiss };
}

describe("AlertCard", () => {
  test("reads Price increase for kind price_increase, not Over limit", () => {
    draw(alert({ kind: "price_increase" }));
    expect(screen.getByText("Price increase")).toBeTruthy();
    expect(screen.queryByText("Over limit")).toBeNull();
  });

  test("shows the proposed limit for suggest_cancel", () => {
    draw(alert({ action: "suggest_cancel", proposed_limit_cents: 8500 }));
    expect(screen.getByText("Proposed limit next month: $85.00")).toBeTruthy();
  });

  test("names the free line Everything else, as the dashboard does", () => {
    draw(alert({ category: "FREE", severity: "medium" }));
    expect(screen.getByText("Everything else")).toBeTruthy();
    expect(screen.queryByText("Free")).toBeNull();
  });

  test("shows no proposed limit when there is none", () => {
    draw(alert({ action: "suggest_cancel" }));
    expect(screen.queryByText(/Proposed limit/)).toBeNull();
  });
  test("shows the alert and calls back with its id", () => {
    const { onAccept, onDismiss } = draw(alert());
    expect(screen.getByText("General merchandise")).toBeTruthy();
    expect(screen.getByText("A $600 purchase put you over.")).toBeTruthy();
    expect(screen.getByText(/one-time/i)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onAccept).toHaveBeenCalledWith(3);
    expect(onDismiss).toHaveBeenCalledWith(3);
  });

  test("a decided alert has no buttons and shows its outcome", () => {
    draw(alert({ status: "dismissed" }));
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("Dismissed")).toBeTruthy();
  });

  test("buttons are disabled while busy", () => {
    draw(alert(), true);
    expect((screen.getByRole("button", { name: "Accept" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Dismiss" }) as HTMLButtonElement).disabled).toBe(true);
  });

  test("the proposed limit shows only for a budget adjustment", () => {
    draw(alert({ action: "adjust_next_budget", cause: "recurring", proposed_limit_cents: 13500 }));
    expect(screen.getByText("Proposed limit next month: $135.00")).toBeTruthy();
    cleanup();
    draw(alert({ proposed_limit_cents: null }));
    expect(screen.queryByText(/Proposed limit/)).toBeNull();
  });

  test("the waiting note shows only when a run is waiting on it", () => {
    draw(alert({ action: "adjust_next_budget", proposed_limit_cents: 13500, awaiting_approval: true }));
    expect(screen.getByText("The agent is waiting for your decision.")).toBeTruthy();
    cleanup();
    draw(alert({ action: "adjust_next_budget", proposed_limit_cents: 13500 }));
    expect(screen.queryByText(/waiting for your decision/)).toBeNull();
  });

  test("shows close's over_income and group_over_target alerts", () => {
    draw(alert({ kind: "over_income", category: null, severity: "medium", cause: null,
                 message: "Your budget lines add up to $5,300.00, $1,100.00 more than your typical income of $4,200.00." }));
    expect(screen.getByText("Budget")).toBeTruthy();
    expect(screen.getByText("Over income")).toBeTruthy();
    expect(screen.getByText(/more than your typical income/)).toBeTruthy();
    expect(screen.queryByText("Ahead of pace")).toBeNull();
    cleanup();
    draw(alert({ kind: "group_over_target", category: null, severity: "medium", cause: null,
                 message: "Needs are 58% of income, above the 50% target." }));
    expect(screen.getByText("Over target")).toBeTruthy();
    expect(screen.getByText("Needs are 58% of income, above the 50% target.")).toBeTruthy();
  });

  test("badges close's goal_ended alert", () => {
    draw(alert({ kind: "goal_ended", category: null, severity: "medium", cause: null,
                 message: "Your Debt payoff goal reached its planned end date, so its $583.00 budget line has ended." }));
    expect(screen.getByText("Goal ended")).toBeTruthy();
    expect(screen.queryByText("Ahead of pace")).toBeNull();
  });
});
