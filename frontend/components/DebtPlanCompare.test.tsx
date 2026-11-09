import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import DebtPlanCompare from "@/components/DebtPlanCompare";
import type { DebtPlan, Plan } from "@/lib/api";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function plan(over: Partial<Plan> = {}): Plan {
  return { strategy: "avalanche", extra_monthly_cents: 0, months: 2, debt_free: "2027-01-01", total_interest_cents: 1114,
           order: [{ account_id: 1, name: "Platinum Card", month: 2, date: "2027-01-01", interest_cents: 1114 }],
           never: [], ...over };
}

function response(extra: number, over: Partial<DebtPlan> = {}): DebtPlan {
  return {
    avalanche: plan({ extra_monthly_cents: extra }),
    snowball: plan({ strategy: "snowball", extra_monthly_cents: extra, total_interest_cents: 1165 }),
    baseline: { avalanche: plan({ months: 5, total_interest_cents: 3000 }),
                snowball: plan({ strategy: "snowball", months: 5, total_interest_cents: 3100 }) },
    excluded: [], start: "2026-12-01", ...over,
  };
}

// renders, then lets the first (mount) request resolve
async function draw(fetchPlan = vi.fn(async (extra: number) => response(extra)), debtsKey = "1:320000") {
  vi.useFakeTimers();
  const view = render(<DebtPlanCompare fetchPlan={fetchPlan} debtsKey={debtsKey} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
  return Object.assign(fetchPlan, { view });
}

async function typeExtra(value: string) {
  fireEvent.change(screen.getByLabelText("Extra per month"), { target: { value } });
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
}

describe("DebtPlanCompare", () => {
  test("shows the suggested extra without changing the input", async () => {
    vi.useFakeTimers();
    render(<DebtPlanCompare suggestedExtraCents={23000} fetchPlan={vi.fn(async (extra: number) => response(extra))} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(300); });
    expect(screen.getByText("You could add about $230.00.")).toBeTruthy();
    expect((screen.getByLabelText("Extra per month") as HTMLInputElement).value).toBe("0");
  });
  test("renders both plans and marks the cheaper one", async () => {
    await draw();
    expect(screen.getByText("Avalanche")).toBeTruthy();
    expect(screen.getByText("Snowball")).toBeTruthy();
    expect(screen.getByText("$11.14")).toBeTruthy();
    expect(screen.getByText("$11.65")).toBeTruthy();
    const tag = screen.getByText("saves the most interest");
    expect(tag.closest("section")?.textContent).toContain("Avalanche");
  });

  test("refetches 300 ms after the input settles", async () => {
    const fetchPlan = await draw();
    fireEvent.change(screen.getByLabelText("Extra per month"), { target: { value: "2" } });
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    fireEvent.change(screen.getByLabelText("Extra per month"), { target: { value: "20" } });
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    await typeExtra("200");
    expect(fetchPlan.mock.calls.map(([cents]) => cents)).toEqual([0, 20000]);
  });

  test("ignores a stale response", async () => {
    let releaseFirst: (p: DebtPlan) => void = () => {};
    const fetchPlan = vi.fn()
      .mockImplementationOnce(async (extra: number) => response(extra))
      .mockImplementationOnce(() => new Promise<DebtPlan>((resolve) => { releaseFirst = resolve; }))
      .mockImplementationOnce(async (extra: number) => response(extra, { avalanche: plan({ months: 7 }) }));
    await draw(fetchPlan);
    await typeExtra("100");                    // slow request
    await typeExtra("300");                    // fast request, answers first: 7 months
    await act(async () => { releaseFirst(response(10000, { avalanche: plan({ months: 99 }) })); });
    expect(screen.getByText("7 months")).toBeTruthy();
    expect(screen.queryByText("99 months")).toBeNull();
  });

  test("shows the difference line only with an extra", async () => {
    await draw();
    expect(screen.queryByText(/months sooner/)).toBeNull();
    await typeExtra("200");
    expect(screen.getByText("+$200/mo: 3 months sooner, $18.86 less interest")).toBeTruthy();
  });

  test("lists excluded debts and a never plan", async () => {
    const never = plan({ months: null, debt_free: null, order: [], never: [{ account_id: 1, name: "Platinum Card" }] });
    await draw(vi.fn(async () => response(0, {
      avalanche: never, snowball: { ...never, strategy: "snowball" },
      excluded: [{ account_id: 2, name: "Store Card", reason: "no_apr" }],
    })));
    expect(screen.getByText("Store Card: no APR on file")).toBeTruthy();
    // review: a long loan can outlast 600 months with its minimum covering the interest, so the message says only
    // what the plan knows
    expect(screen.getAllByText("Platinum Card isn't paid off within 50 years at this pace.")).toHaveLength(2);
  });

  test("all debts excluded shows only the excluded list", async () => {
    const empty = plan({ months: 0, debt_free: null, order: [], total_interest_cents: 0 });
    await draw(vi.fn(async () => response(0, {
      avalanche: empty, snowball: { ...empty, strategy: "snowball" },
      baseline: { avalanche: empty, snowball: { ...empty, strategy: "snowball" } },
      excluded: [{ account_id: 2, name: "Store Card", reason: "no_apr" }],
    })));
    expect(screen.getByText("Store Card: no APR on file")).toBeTruthy();
    expect(screen.queryByText("Avalanche")).toBeNull();
    expect(screen.queryByText(/debt-free/i)).toBeNull();
  });

  // review: linking a bank or a sync changes the debts under a card that's already on screen
  test("refetches when the dashboard's debts change, keeping the extra", async () => {
    const fetchPlan = await draw();
    await typeExtra("200");
    fetchPlan.view.rerender(<DebtPlanCompare fetchPlan={fetchPlan} debtsKey="1:320000|2:1840000" />);
    await act(async () => { await vi.advanceTimersByTimeAsync(300); });
    expect(fetchPlan.mock.calls.map(([cents]) => cents)).toEqual([0, 20000, 20000]);
    expect((screen.getByLabelText("Extra per month") as HTMLInputElement).value).toBe("200");
  });
});


describe("accepted debt-payoff contributions", () => {
  test("includes goal dates and separates goal money from additional extra", async () => {
    await draw(vi.fn(async (extra: number) => response(extra, {
      minimum_monthly_cents: 7500, goal_monthly_cents: 60000, additional_extra_monthly_cents: extra,
      goal_contributions: [{ goal_id: 1, monthly_cents: 60000, start_date: "2026-12-01", target_date: "2027-03-01" }],
    })));
    expect(screen.getByText("Accepted debt-payoff goals included")).toBeTruthy();
    expect(screen.getByText("$600.00/mo, December 2026 through March 2027")).toBeTruthy();
    expect(screen.getByText(/Starting December 2026: \$675.00\/mo/)).toBeTruthy();
    expect((screen.getByLabelText("Extra per month") as HTMLInputElement).value).toBe("0");
    await typeExtra("100");
    expect(screen.getByText(/Starting December 2026: \$775.00\/mo/)).toBeTruthy();
  });

  test("recalculates when accepted goals change even if balances are unchanged", async () => {
    const fetchPlan = await draw();
    fetchPlan.view.rerender(<DebtPlanCompare fetchPlan={fetchPlan} debtsKey="1:320000" goalsKey="goal-accepted" />);
    await act(async () => { await vi.advanceTimersByTimeAsync(300); });
    expect(fetchPlan).toHaveBeenCalledTimes(2);
  });
});
