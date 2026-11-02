import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import GoalsPage from "@/app/goals/page";
import {
  acceptGoal, ApiError, dismissGoal, generateGoals, getGoalRun, getGoals, type Goal, type GoalRun,
} from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getGoals: vi.fn(),
  generateGoals: vi.fn(),
  getGoalRun: vi.fn(),
  acceptGoal: vi.fn(),
  dismissGoal: vi.fn(),
}));

const goalsMock = vi.mocked(getGoals);
const generateMock = vi.mocked(generateGoals);
const runMock = vi.mocked(getGoalRun);
const acceptMock = vi.mocked(acceptGoal);
const dismissMock = vi.mocked(dismissGoal);

function goal(id: number, over: Partial<Goal> = {}): Goal {
  return { id, kind: "debt_payoff", priority: id, monthly_cents: 58300, target_cents: 2160000,
           target_date: "2029-12-01", start_date: "2026-12-01", strategy: "avalanche", rationale: `Because ${id}`,
           status: "proposed", ...over };
}

function run(over: Partial<GoalRun> = {}): GoalRun {
  return { run_id: 7, status: "ok", outcome: null, reason: null, goal_ids: [], waiting_for: [], attempts: 1,
           issues: [], error: null, ...over };
}

// starts a run and lets the page poll through every scripted status, 2 s apart
async function generateThrough(...statuses: GoalRun[]) {
  generateMock.mockResolvedValue({ run_id: 7 });
  statuses.forEach((s) => runMock.mockResolvedValueOnce(s));
  fireEvent.click(screen.getByRole("button", { name: "Generate goals" }));
  for (let i = 0; i < statuses.length; i++) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
  }
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  goalsMock.mockResolvedValue([goal(1)]);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.resetAllMocks();
});

describe("Goals page", () => {
  test("Generate polls until the run settles, then lists the proposal", async () => {
    render(<GoalsPage />);
    await screen.findByText("Because 1");
    goalsMock.mockResolvedValue([goal(1), goal(2, { kind: "savings", strategy: null })]);
    await generateThrough(run({ status: "running" }), run({ status: "awaiting_user", goal_ids: [1, 2], waiting_for: [1, 2] }));
    expect(await screen.findByText("2 goals proposed.")).toBeTruthy();
    expect(await screen.findByText("Because 2")).toBeTruthy();
    expect(runMock).toHaveBeenCalledWith(7);
    expect(generateMock).toHaveBeenCalledWith();
    expect(goalsMock).toHaveBeenCalledWith("current");
    expect(goalsMock.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  test("a run that finished with goals says how many", async () => {
    render(<GoalsPage />);
    await screen.findByText("Because 1");
    await generateThrough(run({ status: "ok", goal_ids: [1] }));
    expect(await screen.findByText("1 goal proposed.")).toBeTruthy();
  });

  test("a run that hasn't reported back asks for a refresh", async () => {
    render(<GoalsPage />);
    await screen.findByText("Because 1");
    generateMock.mockResolvedValue({ run_id: null });
    fireEvent.click(screen.getByRole("button", { name: "Generate goals" }));
    expect(await screen.findByText(/hasn't reported back yet/)).toBeTruthy();
    expect(runMock).not.toHaveBeenCalled();
    expect((screen.getByRole("button", { name: "Generate goals" }) as HTMLButtonElement).disabled).toBe(false);
  });

  test.each([
    ["no_surplus", "No money left over in a typical month"],
    ["nothing_offered", "cover every kind"],
    ["no_affordable_goals", "under $1"],
  ])("outcome %s says why there's no proposal", async (outcome, words) => {
    render(<GoalsPage />);
    await screen.findByText("Because 1");
    await generateThrough(run({ status: "ok", outcome }));
    expect(await screen.findByText((text) => text.includes(words))).toBeTruthy(); // not a regex: "$1" would be an anchor
  });

  test("a failed run says the earlier proposal stands and lists the reasons", async () => {
    render(<GoalsPage />);
    await screen.findByText("Because 1");
    await generateThrough(run({
      status: "error", reason: "validation_failed",
      issues: [{ message: "Goal 1 costs more than the cap.", index: 1, kind: "over_cap" },
               { message: "Goal 2 repeats a kind.", index: 2, kind: "duplicate" }],
    }));
    expect(await screen.findByText(/validation_failed/)).toBeTruthy();
    expect(screen.getByText(/earlier proposal stands/)).toBeTruthy();
    expect(screen.getByText("Goal 1 costs more than the cap.")).toBeTruthy();
    expect(screen.getByText("Goal 2 repeats a kind.")).toBeTruthy();
    expect(screen.getByText("Because 1")).toBeTruthy();
  });

  test("an interrupted run falls back to its error", async () => {
    render(<GoalsPage />);
    await screen.findByText("Because 1");
    await generateThrough(run({ status: "error", reason: null, error: "ConnectionError" }));
    expect(await screen.findByText(/ConnectionError/)).toBeTruthy();
    expect(screen.getByText(/earlier proposal stands/)).toBeTruthy();
  });

  test("goals_changed asks the user to generate again", async () => {
    render(<GoalsPage />);
    await screen.findByText("Because 1");
    await generateThrough(run({ status: "error", reason: "goals_changed" }));
    expect(await screen.findByText(/Generate again/)).toBeTruthy();
    expect(screen.queryByText(/goals_changed/)).toBeNull();
  });

  test("an expired run says a newer proposal replaced it", async () => {
    render(<GoalsPage />);
    await screen.findByText("Because 1");
    await generateThrough(run({ status: "expired" }));
    expect(await screen.findByText("A newer proposal replaced this one.")).toBeTruthy();
  });

  test("a run that never settles stops polling after 10 minutes", async () => {
    render(<GoalsPage />);
    await screen.findByText("Because 1");
    generateMock.mockResolvedValue({ run_id: 7 });
    runMock.mockResolvedValue(run({ status: "running" }));
    fireEvent.click(screen.getByRole("button", { name: "Generate goals" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * 60 * 1000 + 5000);
    });
    expect(await screen.findByText("Still running. Check back later.")).toBeTruthy();
    expect(runMock).toHaveBeenCalledTimes(300);
  });

  test("leaving the page stops polling", async () => {
    const { unmount } = render(<GoalsPage />);
    await screen.findByText("Because 1");
    generateMock.mockResolvedValue({ run_id: 7 });
    runMock.mockResolvedValue(run({ status: "running" }));
    fireEvent.click(screen.getByRole("button", { name: "Generate goals" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    const calls = runMock.mock.calls.length;
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000);
    });
    expect(runMock.mock.calls.length).toBeLessThanOrEqual(calls + 1);
  });

  test("a 409 on Generate shows the server's message and re-enables the button", async () => {
    render(<GoalsPage />);
    await screen.findByText("Because 1");
    generateMock.mockRejectedValue(new ApiError(409, { detail: "A budget or goal run is already in progress." }));
    fireEvent.click(screen.getByRole("button", { name: "Generate goals" }));
    expect(await screen.findByText("A budget or goal run is already in progress.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Generate goals" }) as HTMLButtonElement).disabled).toBe(false);
  });

  test("Accept moves the goal to Accepted after a reload", async () => {
    render(<GoalsPage />);
    await screen.findByText("Because 1");
    expect(screen.getByRole("heading", { name: "Proposed" })).toBeTruthy();
    acceptMock.mockResolvedValue(goal(1, { status: "accepted" }));
    goalsMock.mockResolvedValue([goal(1, { status: "accepted" })]);
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(acceptMock).toHaveBeenCalledWith(1));
    expect(await screen.findByRole("heading", { name: "Accepted" })).toBeTruthy();
    expect(screen.getByText("In your budget from December 2026")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Proposed" })).toBeNull();
  });

  test("Stop on an accepted goal dismisses it", async () => {
    goalsMock.mockResolvedValue([goal(1, { status: "accepted" })]);
    render(<GoalsPage />);
    await screen.findByText("Because 1");
    dismissMock.mockResolvedValue(goal(1, { status: "dismissed" }));
    goalsMock.mockResolvedValue([]);
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(dismissMock).toHaveBeenCalledWith(1));
    expect(await screen.findByText(/No goals yet/)).toBeTruthy();
  });

  test("a 409 on Accept shows the message and reloads, dropping the stale goal", async () => {
    render(<GoalsPage />);
    await screen.findByText("Because 1");
    acceptMock.mockRejectedValue(new ApiError(409, { detail: "This proposal is older. Generate goals again." }));
    goalsMock.mockResolvedValue([]);
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    expect(await screen.findByText("This proposal is older. Generate goals again.")).toBeTruthy();
    await waitFor(() => expect(screen.queryByText("Because 1")).toBeNull());
    // the reload must not wipe the message
    expect(screen.getByText("This proposal is older. Generate goals again.")).toBeTruthy();
  });

  test("no goals yet shows the empty state", async () => {
    goalsMock.mockResolvedValue([]);
    render(<GoalsPage />);
    expect(await screen.findByText(/No goals yet/)).toBeTruthy();
  });

  test("a failed load says so", async () => {
    goalsMock.mockRejectedValue(new ApiError(500, { detail: "Database is down." }));
    render(<GoalsPage />);
    expect(await screen.findByText("Database is down.")).toBeTruthy();
  });
});
