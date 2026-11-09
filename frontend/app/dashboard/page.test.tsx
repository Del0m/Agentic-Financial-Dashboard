import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import DashboardPage from "@/app/dashboard/page";
import {
  ApiError, disconnectBank, getAlerts, getBudget, getDashboard, getForecast, getGoals, getRecurring, getSim, getTransactions, type Budget, type Dashboard, type Goal,
} from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getDashboard: vi.fn(),
  disconnectBank: vi.fn(),
  getBudget: vi.fn(),
  getAlerts: vi.fn(),
  getTransactions: vi.fn(),
  getGoals: vi.fn(),
  getRecurring: vi.fn(),
  getForecast: vi.fn(),
  getSim: vi.fn(),
}));
// these draw charts, open Plaid or call the planner; none of it matters here
vi.mock("@/components/BalanceChart", () => ({ default: () => null }));
vi.mock("@/components/PlaidLinkButton", () => ({ default: ({ label, onError }: { label: string; onError: (message: string) => void }) => <button type="button" onClick={() => onError("A bank is already linked.")}>{label}</button> }));
vi.mock("@/components/DebtPlanCompare", () => ({ default: () => null }));
vi.mock("@/components/ForecastChart", () => ({ default: () => null }));

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
  vi.mocked(getSim).mockRejectedValue(new ApiError(404, { detail: "Not Found" }));
  vi.mocked(getDashboard).mockResolvedValue(DASHBOARD);
  budgetMock.mockResolvedValue(BUDGET);
  vi.mocked(getAlerts).mockResolvedValue([]);
  vi.mocked(getTransactions).mockResolvedValue([]);
  goalsMock.mockResolvedValue([]);
  vi.mocked(getRecurring).mockResolvedValue({ as_of: null, streams: [] });
  vi.mocked(getForecast).mockResolvedValue({ today: "2026-10-10", status: "no_history", start_cents: 10000,
    buffer_cents: 10000, horizon_days: 0, income: "none", days: [], events: [], dip: null,
    monthly_surplus_cents: null, surplus_source: "measured", suggested_extra_cents: null, notes: [] });
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
  test("does not expose a user switch", async () => {
    render(<DashboardPage />);
    await screen.findByText("Cash forecast");
    expect(screen.queryByRole("group", { name: "Whose data to show" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Demo user" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Plaid bank" })).toBeNull();
  });

  test("shows the Cash forecast card", async () => {
    render(<DashboardPage />);
    expect(await screen.findByRole("heading", { name: "Cash forecast" })).toBeTruthy();
  });

  test("keeps a forecast failure inside its card", async () => {
    vi.mocked(getForecast).mockRejectedValue(new Error("offline"));
    render(<DashboardPage />);
    expect(await screen.findByText("Couldn't load your cash forecast: offline")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Recurring charges" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Goals" })).toBeTruthy();
  });
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
    expect(await screen.findByText("$583.00/mo planned")).toBeTruthy();
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

describe("Dashboard demo user", () => {
  test("an existing connection has no repeat-link button", async () => {
    render(<DashboardPage />);
    expect(await screen.findByText("Bank connected")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Link another account" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Connect a bank" })).toBeNull();
    expect(screen.queryByText(/^Demo ·/)).toBeNull();
  });

  test("a simulated user gets no Link button, and sees its simulated date", async () => {
    vi.mocked(getSim).mockResolvedValue({ simulated: true, profile: "overspend-recurring", date: "2026-10-08" });
    render(<DashboardPage />);
    expect(await screen.findByText("Demo · Oct 8, 2026")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Link another account" })).toBeNull();
  });

  test("the budget card lists an accepted goal that starts after this month", async () => {
    goalsMock.mockResolvedValue([goal(2, { kind: "savings", strategy: null, monthly_cents: 20000, start_date: "2027-01-01" })]);
    render(<DashboardPage />);
    expect(await screen.findByText("$200.00/mo from January 2027")).toBeTruthy();
  });

  test("the budget card explains the ring and the list", async () => {
    render(<DashboardPage />);
    expect(await screen.findByText(/Day 10 of 31\. The ring shows how your monthly budget is split/)).toBeTruthy();
    expect(screen.getByRole("img", { name: /Budget split/ })).toBeTruthy();
  });
});


describe("Plaid connection state", () => {
  test("a fresh page mount waits for saved connection state before offering Link", async () => {
    const linked = { ...DASHBOARD, bank_linked: true };
    vi.mocked(getDashboard).mockResolvedValueOnce(linked);
    const first = render(<DashboardPage />);
    expect(await screen.findByText("Bank connected")).toBeTruthy();
    first.unmount();

    let resolve!: (dashboard: Dashboard) => void;
    vi.mocked(getDashboard).mockReturnValueOnce(new Promise<Dashboard>((done) => { resolve = done; }));
    render(<DashboardPage />);
    expect(screen.queryByRole("button", { name: "Connect a bank" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Link another account" })).toBeNull();
    await act(async () => resolve(linked));
    expect(await screen.findByText("Bank connected")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Connect a bank" })).toBeNull();
  });

  test("a stale unlinked tab reloads connection state after Link is refused", async () => {
    vi.mocked(getDashboard)
      .mockResolvedValueOnce({ ...DASHBOARD, bank_linked: false, accounts: [], debts: [] })
      .mockResolvedValue({ ...DASHBOARD, bank_linked: true });
    render(<DashboardPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Connect a bank" }));
    expect(await screen.findByText("Bank connected")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Connect a bank" })).toBeNull();
    expect(getDashboard).toHaveBeenCalledTimes(2);
  });

  test("an unlinked user is prompted to connect, without financial cards", async () => {
    vi.mocked(getDashboard).mockResolvedValue({ ...DASHBOARD, bank_linked: false, accounts: [], debts: [] });
    render(<DashboardPage />);
    expect(await screen.findByRole("button", { name: "Connect a bank" })).toBeTruthy();
    expect(screen.getByText(/No bank is linked yet/)).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Budget" })).toBeNull();
  });

  test("a linked bank with no imported accounts offers retry instead of another link", async () => {
    vi.mocked(getDashboard).mockResolvedValue({ ...DASHBOARD, bank_linked: true, accounts: [], debts: [],
      unsynced_items: [{ id: 4, institution_name: "Test Bank", syncing: false }] });
    render(<DashboardPage />);
    expect(await screen.findByRole("heading", { name: "Bank already connected" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry sync" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Connect a bank" })).toBeNull();
  });
});


describe("Sandbox disconnect", () => {
  test("opening and cancelling confirmation does not disconnect", async () => {
    vi.mocked(getDashboard).mockResolvedValue({ ...DASHBOARD, bank_linked: true, can_disconnect: true });
    render(<DashboardPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Disconnect bank" }));
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    expect(disconnectBank).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(disconnectBank).not.toHaveBeenCalled();
  });

  test("confirmed disconnect returns to Connect a bank without old financial cards", async () => {
    vi.mocked(getDashboard).mockResolvedValueOnce({ ...DASHBOARD, bank_linked: true, can_disconnect: true })
      .mockResolvedValue({ ...DASHBOARD, bank_linked: false, can_disconnect: false, accounts: [], debts: [] });
    vi.mocked(disconnectBank).mockResolvedValue({ disconnected: true });
    render(<DashboardPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Disconnect bank" }));
    fireEvent.click(screen.getByRole("button", { name: "Disconnect and clear demo data" }));
    expect(await screen.findByRole("button", { name: "Connect a bank" })).toBeTruthy();
    expect(disconnectBank).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("heading", { name: "Budget" })).toBeNull();
  });

  test("a refused disconnect leaves the connection visible and reports the error", async () => {
    vi.mocked(getDashboard).mockResolvedValue({ ...DASHBOARD, bank_linked: true, can_disconnect: true });
    vi.mocked(disconnectBank).mockRejectedValue(new ApiError(409, { detail: "Chat is still running." }));
    render(<DashboardPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Disconnect bank" }));
    fireEvent.click(screen.getByRole("button", { name: "Disconnect and clear demo data" }));
    expect(await screen.findByText("Chat is still running.")).toBeTruthy();
    expect(screen.getByText("Bank connected")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Connect a bank" })).toBeNull();
  });

  test("disconnect is hidden when unavailable", async () => {
    vi.mocked(getDashboard).mockResolvedValue({ ...DASHBOARD, bank_linked: true, can_disconnect: false });
    render(<DashboardPage />);
    expect(await screen.findByText("Bank connected")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Disconnect bank" })).toBeNull();
  });
});
