import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import DashboardPage from "@/app/dashboard/page";
import {
  ApiError, getAlerts, getBudget, getDashboard, getGoals, getRecurring, getTransactions, type Budget, type Dashboard, type Goal,
} from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getDashboard: vi.fn(),
  getBudget: vi.fn(),
  getAlerts: vi.fn(),
  getTransactions: vi.fn(),
  getGoals: vi.fn(),
  getRecurring: vi.fn(),
}));
// these draw charts, open Plaid or call the planner; none of it matters here
vi.mock("@/components/BalanceChart", () => ({ default: () => null }));
vi.mock("@/components/PlaidLinkButton", () => ({ default: () => null }));
vi.mock("@/components/DebtPlanCompare", () => ({ default: () => null }));

const goalsMock = vi.mocked(getGoals);
const budgetMock = vi.mocked(getBudget);

const DASHBOARD: Dashboard = {
  net_worth_cents: 100000, assets_cents: 100000, debts_cents: 0, accounts_missing_balance: 0, debts: [],
  unsynced_items: [],
  accounts: [{ id: 1, name: "Checking", mask: "0000", type: "depository", group: "asset", subtype: "checking",
               current_balance_cents: 100000, available_balance_cents: 100000, institution_name: "Bank" }],
};

const BUDGET: Budget = {
  period: { start: "2026-12-01", end: "2026-12-31" }, ended_period: null, days_elapsed: 10, days_in_period: 31,
  unbudgeted: [], income_cents: 500000, total_cents: 500000, style: "fifty_thirty_twenty",
  lines: [{ id: 1, category: "GOAL_DEBT_PAYOFF", name: "Debt payoff", group: "goal", kind: "fixed", limit_cents: 58300,
            spent_cents: 0, pace: null, flagged: false, reason: null }],
};

function goal(id: number, over: Partial<Goal> = {}): Goal {
  return { id, kind: "debt_payoff", priority: id, monthly_cents: 58300, target_cents: 2160000,
           target_date: "2029-12-01", start_date: "2026-12-01", strategy: "avalanche", rationale: "r",
           status: "accepted", ...over };
}

beforeEach(() => {
  vi.mocked(getDashboard).mockResolvedValue(DASHBOARD);
  budgetMock.mockResolvedValue(BUDGET);
  vi.mocked(getAlerts).mockResolvedValue([]);
  vi.mocked(getTransactions).mockResolvedValue([]);
  goalsMock.mockResolvedValue([]);
  vi.mocked(getRecurring).mockResolvedValue({ as_of: null, streams: [] });
});

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

async function goalsCard() {
  const heading = await screen.findByRole("heading", { name: "Goals" });
  return within(heading.closest("section") as HTMLElement);
}

describe("Dashboard goals", () => {
  test("shows the Recurring charges card", async () => {
    render(<DashboardPage />);
    expect(await screen.findByRole("heading", { name: "Recurring charges" })).toBeTruthy();
  });

  test("keeps a recurring failure inside its card", async () => {
    vi.mocked(getRecurring).mockRejectedValue(new Error("offline"));
    render(<DashboardPage />);
    expect(await screen.findByText(/Couldn't load your recurring charges: offline/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Goals" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Transactions" })).toBeTruthy();
  });

  test("shows the empty text for a user with no streams", async () => {
    render(<DashboardPage />);
    expect(await screen.findByText("No repeating charges found yet.")).toBeTruthy();
  });
  test("accepted goals appear in a Goals card with their start month", async () => {
    goalsMock.mockResolvedValue([goal(1), goal(2, { kind: "savings", strategy: null, monthly_cents: 20000, start_date: "2027-01-01" })]);
    render(<DashboardPage />);
    const card = await goalsCard();
    expect(await card.findByText("Debt payoff (avalanche)")).toBeTruthy();
    expect(card.getByText("$583.00/mo")).toBeTruthy();
    expect(card.getByText("From December 2026")).toBeTruthy();
    expect(card.getByText("Savings")).toBeTruthy();
    expect(card.getByText("From January 2027")).toBeTruthy();
    expect(goalsMock).toHaveBeenCalledWith("current");
  });

  test("proposed goals aren't listed in the card", async () => {
    goalsMock.mockResolvedValue([goal(1, { status: "proposed" })]);
    render(<DashboardPage />);
    const card = await goalsCard();
    expect(await card.findByText(/No active goals right now/)).toBeTruthy();
    expect(card.queryByText("Debt payoff (avalanche)")).toBeNull();
  });

  test("a goals failure stays inside its card", async () => {
    goalsMock.mockRejectedValue(new ApiError(500, { detail: "Goals are down." }));
    render(<DashboardPage />);
    const card = await goalsCard();
    expect(await card.findByText(/Goals are down\./)).toBeTruthy();
    // the budget card and the header still work
    expect(await screen.findByText("$583.00/mo")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Goals" })).toBeTruthy();
  });

  test("the header link counts proposed goals", async () => {
    goalsMock.mockResolvedValue([goal(1, { status: "proposed" }), goal(2, { status: "proposed", kind: "savings" }), goal(3)]);
    render(<DashboardPage />);
    const link = await screen.findByRole("link", { name: "Goals (2)" });
    expect(link.getAttribute("href")).toBe("/goals");
  });

  test("with nothing proposed the link has no count", async () => {
    render(<DashboardPage />);
    expect((await screen.findByRole("link", { name: "Goals" })).getAttribute("href")).toBe("/goals");
  });
});
