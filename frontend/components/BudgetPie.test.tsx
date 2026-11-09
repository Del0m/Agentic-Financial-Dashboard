import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";
import BudgetPie, { slicesOf, upcomingGoals } from "@/components/BudgetPie";
import type { Budget, BudgetLine, Goal } from "@/lib/api";

afterEach(cleanup);

function line(over: Partial<BudgetLine> = {}): BudgetLine {
  return { id: 1, category: "FOOD_AND_DRINK", name: "Food and drink", group: "needs", kind: "flexible",
           limit_cents: 47200, spent_cents: 23600, pace: 0.8, flagged: false, reason: null, ...over };
}

function budget(lines: BudgetLine[]): Budget {
  return { period: { start: "2026-10-01", end: "2026-10-31" }, ended_period: null, days_elapsed: 20, days_in_period: 31,
           lines, unbudgeted: [], income_cents: 420000, total_cents: 420000, style: "caps" };
}

const DEMO = [
  line({ id: 1, category: "PERSONAL_CARE", name: "Personal care", group: "wants", limit_cents: 4200, spent_cents: 9000,
         flagged: true, reason: "over_limit" }),
  line({ id: 2, category: "RENT_AND_UTILITIES", name: "Rent and utilities", kind: "fixed", limit_cents: 150000, spent_cents: 150000, pace: null }),
  line({ id: 3, category: "SAVINGS_TRANSFER", name: "Savings", group: "saving", kind: "fixed", limit_cents: 50000, spent_cents: 0, pace: null }),
  line({ id: 4, category: "FREE", name: "Free", group: "free", limit_cents: 215800, spent_cents: 120000, flagged: true, reason: "pace" }),
];

function goal(over: Partial<Goal> = {}): Goal {
  return { id: 1, kind: "emergency_fund", priority: 1, monthly_cents: 170400, target_cents: 450000, target_date: "2027-01-01",
           start_date: "2026-11-01", strategy: null, rationale: "r", status: "accepted", ...over };
}

const draw = (lines: BudgetLine[]) => render(<BudgetPie budget={budget(lines)} />);

describe("BudgetPie", () => {
  test("one slice per line, sized by its share of the plan", () => {
    draw(DEMO);
    const slices = screen.getAllByTestId("slice");
    expect(slices.map((s) => s.getAttribute("data-key"))).toEqual(["PERSONAL_CARE", "RENT_AND_UTILITIES", "SAVINGS_TRANSFER", "FREE"]);
    const C = 2 * Math.PI * 75;
    const rent = Number(slices[1].getAttribute("stroke-dasharray")!.split(" ")[0]);
    expect(rent).toBeCloseTo((150000 / 420000) * C - 2, 5);
    expect(screen.getByRole("img").getAttribute("aria-label")).toMatch(/Rent and utilities \$1,500\.00 \(36%\)/);
  });

  test("the centre says how much is spent of the whole plan", () => {
    draw(DEMO);
    expect(screen.getByText("$2,790.00")).toBeTruthy();
    expect(screen.getByText("spent of $4,200.00")).toBeTruthy();
  });

  test("the list says each line's spending in words", () => {
    draw(DEMO);
    expect(screen.getByText(/\$90\.00 of \$42\.00/)).toBeTruthy();
    expect(screen.getByText(/\$48\.00 over/).className).toMatch(/red/);
    expect(screen.getByText(/\$958\.00 left · spending fast/).className).toMatch(/amber/);
    expect(screen.getByText("Everything else")).toBeTruthy();
    expect(screen.getAllByText("(fixed)")).toHaveLength(2);
  });

  test("an unflagged line says how much is left", () => {
    draw([line()]);
    expect(screen.getByText(/\$236\.00 left/)).toBeTruthy();
    expect(screen.queryByText(/pace/)).toBeNull();
  });

  test("fixed and saving lines show no left or over", () => {
    draw([DEMO[1], DEMO[2]]);
    expect(screen.queryByText(/left|over/)).toBeNull();
  });

  test("hovering a slice names it in the centre, and highlights its row", () => {
    draw(DEMO);
    fireEvent.mouseEnter(screen.getAllByTestId("slice")[1]);
    expect(screen.getByText("$1,500.00 · 36%")).toBeTruthy();
    expect(screen.queryByText("spent of $4,200.00")).toBeNull();
    const row = screen.getAllByText("Rent and utilities").map((e) => e.closest("li")).find(Boolean)!;
    expect(row.className).toMatch(/bg-black/);
    fireEvent.mouseLeave(screen.getAllByTestId("slice")[1]);
    expect(screen.getByText("spent of $4,200.00")).toBeTruthy();
  });

  test("focusing a row does the same from the keyboard", () => {
    draw(DEMO);
    fireEvent.focus(screen.getByText("Savings").closest("li")!);
    expect(screen.getByText("$500.00 · 12%")).toBeTruthy();
  });

  test("a goal line is a slice of the plan with no spending", () => {
    draw([line(), line({ id: 2, category: "GOAL_DEBT_PAYOFF", name: "Debt payoff", group: "goal", kind: "fixed",
                         limit_cents: 58300, spent_cents: 0, pace: null })]);
    expect(screen.getAllByTestId("slice")).toHaveLength(2);
    expect(screen.getByText("$583.00/mo planned")).toBeTruthy();
    expect(screen.queryByText(/\$0\.00 of \$583\.00/)).toBeNull();
  });

  test("a zero limit gets no slice but stays in the list", () => {
    draw([line(), line({ id: 2, category: "TRAVEL", name: "Travel", limit_cents: 0, spent_cents: 0 })]);
    expect(screen.getAllByTestId("slice")).toHaveLength(1);
    expect(screen.getByText("Travel")).toBeTruthy();
  });

  test("a single line fills the whole ring", () => {
    draw([line()]);
    const C = 2 * Math.PI * 75;
    expect(Number(screen.getByTestId("slice").getAttribute("stroke-dasharray")!.split(" ")[0])).toBeCloseTo(C, 5);
  });

  test("past seven lines, the smallest share one grey slice", () => {
    const many = Array.from({ length: 9 }, (_, i) => line({ id: i, category: `C${i}`, name: `Line ${i}`, limit_cents: (i + 1) * 1000 }));
    const slices = slicesOf(many);
    expect(slices).toHaveLength(7);
    expect(slices[6]).toMatchObject({ key: "other", cents: 1000 + 2000 + 3000, color: "var(--slice-other)" });
    expect(slices.slice(0, 6).map((s) => s.color)).toEqual([1, 2, 3, 4, 5, 6].map((n) => `var(--slice-${n})`));
  });

  test("a renamed free line keeps its name", () => {
    draw([line({ category: "FREE", name: "Fun money", group: "free" })]);
    expect(screen.getByText("Fun money")).toBeTruthy();
  });
  test("an accepted goal that starts next month is listed under the ring, with no slice", () => {
    render(<BudgetPie budget={budget(DEMO)} goals={[goal()]} />);
    expect(screen.getByText(/Accepted goals join your budget when their month starts/)).toBeTruthy();
    expect(screen.getByText("Emergency fund")).toBeTruthy();
    expect(screen.getByText("$1,704.00/mo from November 2026")).toBeTruthy();
    expect(screen.getAllByTestId("slice")).toHaveLength(4);
  });

  test("only accepted goals that start after this month count as upcoming", () => {
    const b = budget(DEMO);
    expect(upcomingGoals(b, [
      goal({ id: 1 }),
      goal({ id: 2, start_date: "2026-10-01" }), // already a line in this month's budget
      goal({ id: 3, status: "proposed" }),
      goal({ id: 4, start_date: null }),
    ]).map((g) => g.id)).toEqual([1]);
    expect(upcomingGoals({ ...b, period: null }, [goal()])).toEqual([]);
  });

  test("no upcoming goals, no extra list", () => {
    draw(DEMO);
    expect(screen.queryByText(/Accepted goals join/)).toBeNull();
  });

  test("an approved limit is listed for next month, and this month keeps its limit", () => {
    const b = { ...budget(DEMO), next_start: "2026-11-01",
                next_limits: [{ category: "PERSONAL_CARE", name: "Personal care", limit_cents: 4200, next_cents: 13500 }] };
    render(<BudgetPie budget={b} />);
    expect(screen.getByText("Changes you approved, starting November 2026:")).toBeTruthy();
    expect(screen.getByText("$42.00 now → $135.00 next month")).toBeTruthy();
    expect(screen.getByText("$90.00 of $42.00")).toBeTruthy(); // october's row is unchanged
    expect(screen.getByText(/\$48.00 over/)).toBeTruthy();
    expect(screen.getAllByText("Personal care")).toHaveLength(2);
  });

  test("no approved limits, no next-month list", () => {
    render(<BudgetPie budget={{ ...budget(DEMO), next_start: "2026-11-01", next_limits: [] }} />);
    expect(screen.queryByText(/Changes you approved/)).toBeNull();
  });
});
