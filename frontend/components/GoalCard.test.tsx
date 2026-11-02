import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import GoalCard from "@/components/GoalCard";
import type { Goal } from "@/lib/api";

afterEach(cleanup);

function goal(over: Partial<Goal> = {}): Goal {
  return { id: 4, kind: "debt_payoff", priority: 1, monthly_cents: 58300, target_cents: 2160000,
           target_date: "2029-12-01", start_date: "2026-12-01", strategy: "avalanche",
           rationale: "The card at 24.99% costs the most, so it goes first.", status: "proposed", ...over };
}

function draw(g: Goal, busy = false) {
  const onAccept = vi.fn(), onDismiss = vi.fn();
  render(<GoalCard goal={g} busy={busy} onAccept={onAccept} onDismiss={onDismiss} />);
  return { onAccept, onDismiss };
}

describe("GoalCard", () => {
  test("a proposed goal shows its numbers and both decisions", () => {
    const { onAccept, onDismiss } = draw(goal());
    expect(screen.getByText("Debt payoff (avalanche)")).toBeTruthy();
    expect(screen.getByText("$583.00/mo")).toBeTruthy();
    expect(screen.getByText(/\$21,600\.00/)).toBeTruthy();
    expect(screen.getByText(/Dec 1, 2029/)).toBeTruthy();
    expect(screen.getByText("The card at 24.99% costs the most, so it goes first.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onAccept).toHaveBeenCalledWith(4);
    expect(onDismiss).toHaveBeenCalledWith(4);
  });

  test("an accepted goal says when it joins the budget and offers Stop", () => {
    const { onDismiss } = draw(goal({ status: "accepted", kind: "emergency_fund", strategy: null }));
    expect(screen.getByText("Emergency fund")).toBeTruthy();
    expect(screen.getByText("In your budget from December 2026")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Dismiss" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(onDismiss).toHaveBeenCalledWith(4);
    expect(screen.getByText(/ends at the next close/)).toBeTruthy();
  });

  test("buttons are disabled while busy", () => {
    draw(goal(), true);
    expect((screen.getByRole("button", { name: "Accept" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Dismiss" }) as HTMLButtonElement).disabled).toBe(true);
    cleanup();
    draw(goal({ status: "accepted" }), true);
    expect((screen.getByRole("button", { name: "Stop" }) as HTMLButtonElement).disabled).toBe(true);
  });

  test("a proposal with no start date can only be dismissed", () => {
    draw(goal({ start_date: null }));
    expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeTruthy();
    expect(screen.getByText(/Generate goals again/)).toBeTruthy();
  });

  test("a goal with no target date still shows its target", () => {
    draw(goal({ kind: "savings", strategy: null, target_date: null, target_cents: 100000 }));
    expect(screen.getByText("Savings")).toBeTruthy();
    expect(screen.getByText(/\$1,000\.00/)).toBeTruthy();
    expect(screen.queryByText(/ by /)).toBeNull();
  });
});
