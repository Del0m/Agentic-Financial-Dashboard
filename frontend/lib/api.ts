import { parseSSE } from "@/lib/sse";

// typed calls to the fastapi backend through the /api proxy (see next.config.ts).
// field names mirror the backend's pydantic models exactly.

export type Account = {
  id: number;
  name: string;
  mask: string | null;
  type: string;
  group: "asset" | "debt" | "other";
  subtype: string | null;
  current_balance_cents: number | null;
  available_balance_cents: number | null;
  institution_name: string | null;
};

export type Debt = {
  account_id: number;
  name: string;
  mask: string | null;
  kind: string;
  apr: number | null;
  balance_cents: number | null;
  min_payment_cents: number | null;
  next_due_date: string | null;
};

export type Dashboard = {
  net_worth_cents: number;
  assets_cents: number;
  debts_cents: number;
  accounts_missing_balance: number;
  accounts: Account[];
  debts: Debt[];
  // linked items whose first sync never finished; each gets a Retry sync button
  unsynced_items: { id: number; institution_name: string | null; syncing: boolean }[];
};

export type ExchangeResult = { item_id: number; synced: boolean; syncing: boolean };

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(`request failed (${status})`);
    this.status = status;
    this.detail = detail;
  }
}

// one readable line for any failure: fastapi's {detail}, a plaid error code, or a network error
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const detail = (err.detail as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string") return detail;
    const code = (detail as { plaid_error_code?: string } | undefined)?.plaid_error_code;
    if (code) return `Plaid error ${code}`;
    return `Request failed (${err.status})`;
  }
  return err instanceof Error ? err.message : String(err);
}

// read as text first: a proxy error page is html, and res.json() would throw a SyntaxError
async function readBody(res: Response): Promise<unknown> {
  const text = await res.text();
  try {
    return text ? JSON.parse(text) : null;
  } catch {
    return text; // not json; keep the raw text as the detail
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  const body = await readBody(res);
  if (!res.ok) throw new ApiError(res.status, body);
  return body as T;
}

export function getDashboard(): Promise<Dashboard> {
  return request<Dashboard>("/dashboard");
}

export async function createLinkToken(): Promise<string> {
  const { link_token } = await request<{ link_token: string }>("/plaid/link-token", { method: "POST" });
  return link_token;
}

export function exchangePublicToken(publicToken: string, institutionName?: string | null): Promise<ExchangeResult> {
  return request<ExchangeResult>("/plaid/exchange", {
    method: "POST",
    body: JSON.stringify({ public_token: publicToken, institution_name: institutionName ?? null }),
  });
}

export async function syncItem(itemId: number): Promise<void> {
  await request(`/plaid/items/${itemId}/sync`, { method: "POST" });
}

// the budget agent (tpc-20): live pace for the dashboard, runs started from the alerts page, and the
// alerts the user accepts or dismisses

export type BudgetLine = {
  id: number;
  category: string;
  name: string; // the user's label; the budget rules read the group (tpc-22)
  group: "needs" | "wants" | "saving" | "free" | "goal"; // goal: an accepted goal (tpc-25); no spending to measure
  kind: "fixed" | "flexible";
  limit_cents: number;
  spent_cents: number;
  pace: number | null;
  flagged: boolean;
  reason: "pace" | "over_limit" | null;
};

export type Budget = {
  period: { start: string; end: string } | null; // null: no budget covers today
  ended_period: { start: string; end: string } | null; // the latest budget, when today is past it
  days_elapsed: number;
  days_in_period: number;
  lines: BudgetLine[];
  unbudgeted: { category: string; spent_cents: number }[];
  income_cents: number | null;
  total_cents: number | null;
  style: string;
  // limits the user approved for next month; month close applies them on next_start
  next_limits?: { category: string; name: string; limit_cents: number; next_cents: number }[];
  next_start?: string | null;
};

export type Alert = {
  id: number;
  stream_id: number | null;
  kind: string;
  category: string | null;
  severity: string;
  cause: string | null;
  action: string | null;
  message: string;
  proposed_limit_cents: number | null; // adjust_next_budget or suggest_cancel
  status: "pending_user" | "accepted" | "dismissed";
  created_at: string;
  period_start: string | null;
  awaiting_approval: boolean; // a paused budget run is waiting on this decision
};

export type RecurringStream = {
  id: number;
  direction: "in" | "out";
  label: string;
  frequency: "weekly" | "biweekly" | "semi_monthly" | "monthly";
  amount_kind: "fixed" | "varies";
  typical_cents: number;
  last_cents: number;
  previous_cents: number | null;
  last_date: string;
  next_date: string;
  status: "active" | "stale";
  change: "new" | "price_increase" | "income_change" | null;
  first_seen: string;
};

export type RecurringResponse = { as_of: string | null; streams: RecurringStream[] };

export function getRecurring(): Promise<RecurringResponse> {
  return request<RecurringResponse>("/recurring");
}

export type RunStatus = {
  run_id: number;
  status: "running" | "ok" | "no_budget" | "period_ended" | "awaiting_user" | "error";
  alert_ids: number[];
  waiting_for: number[];
  error: string | null;
};

export function getBudget(): Promise<Budget> {
  return request<Budget>("/budget");
}

// starts a run in the background (it can outlast the proxy's 30 s); poll getRun with the id
export function runBudget(date?: string): Promise<{ run_id: number | null }> {
  return request("/budget/run", { method: "POST", body: JSON.stringify(date ? { date } : {}) });
}

export function getRun(runId: number): Promise<RunStatus> {
  return request<RunStatus>(`/budget/runs/${runId}`);
}

// what the budget agent did with one flagged line: code's measurement, the memory it was given, the model's
// diagnosis and choice, and what code's checks did with that choice
export type TraceFlag = {
  category: string;
  reason: "pace" | "over_limit";
  group?: string | null;
  spent_cents?: number;
  limit_cents?: number;
  pace?: number | null;
  days_elapsed?: number;
  days_in_period?: number;
  projected_cents?: number;
  largest_cents?: number | null;
  largest_share?: number;
  earlier_alerts?: { severity: string; action: string; status: string }[];
  past_advice?: { month: string; action: string; outcome: string | null }[];
  graph_facts?: boolean;
  cause?: string | null;
  evidence?: string | null;
  model_action: string | null;
  action: string | null;
  override: "fallback" | "downgraded" | "escalated" | "reclassified" | "cut_dismissed" | null;
  proposed_limit_cents: number | null;
  alert_id: number | null;
};

export type TraceSignal = {
  kind: string;
  category: string | null;
  shortfall_cents: number | null;
  days_ahead: number | null;
  typical_cents: number | null;
  frequency: string | null;
  model_action: string | null;
  action: string | null;
  fallback: boolean | null;
  alert_id: number | null;
};

export type ActivityRun = {
  run_id: number;
  status: RunStatus["status"];
  started_at: string | null;
  finished_at: string | null;
  as_of: string | null;
  steps: string[];
  summary: {
    trace?: { lines_checked: number; flags: TraceFlag[]; signals: TraceSignal[] };
    flags?: { category: string; reason: string }[];
    alert_ids?: number[];
    llm_calls?: number;
    llm_failures?: number;
    streams?: { count: number; new: number[]; price_up: number[]; income_change: number[] };
    forecast?: { ok?: boolean; low_balance?: { date: string; shortfall_cents: number } | null } | null;
    sync?: { skipped?: boolean; items?: number; errors?: string[]; added?: number } | null;
    error?: string;
  };
};

// the newest budget runs, newest first, with each one's decision trace and (live) the steps it has started
export function getActivity(limit = 5): Promise<ActivityRun[]> {
  return request<ActivityRun[]>(`/budget/activity?limit=${limit}`);
}

// the payoff planner (tpc-32): every number comes from the backend's simulation
export type PlanPayoff = { account_id: number; name: string; month: number; date: string; interest_cents: number };

export type Plan = {
  strategy: "avalanche" | "snowball";
  extra_monthly_cents: number;
  months: number | null;            // null when a debt is never paid off
  debt_free: string | null;
  total_interest_cents: number | null;   // null when never: there's no total to show
  order: PlanPayoff[];
  never: { account_id: number; name: string }[];   // debts never paid off: not in order
};

export type PlanExcluded = { account_id: number; name: string; reason: "no_apr" | "no_minimum" | "mortgage" };

export type DebtPlan = {
  avalanche: Plan;
  snowball: Plan;
  baseline: { avalanche: Plan; snowball: Plan };
  excluded: PlanExcluded[];
  start: string;
};

export function debtPlan(extraMonthlyCents: number, includeMortgage = false): Promise<DebtPlan> {
  return request<DebtPlan>("/debt-plan", {
    method: "POST",
    body: JSON.stringify({ extra_monthly_cents: extraMonthlyCents, include_mortgage: includeMortgage }),
  });
}

export function getAlerts(status: "pending_user" | "all" = "all"): Promise<Alert[]> {
  return request<Alert[]>(`/alerts?status=${status}`);
}

export function acceptAlert(id: number): Promise<Alert> {
  return request<Alert>(`/alerts/${id}/accept`, { method: "POST" });
}

export function dismissAlert(id: number): Promise<Alert> {
  return request<Alert>(`/alerts/${id}/dismiss`, { method: "POST" });
}

// the goals the agent proposed (tpc-23) and the user decides on (tpc-25)
export type Goal = {
  id: number;
  kind: "debt_payoff" | "emergency_fund" | "savings";
  priority: number;
  monthly_cents: number;
  target_cents: number;
  target_date: string | null;
  start_date: string | null; // null: from before tpc-25, so it can be dismissed but not accepted
  strategy: string | null;
  rationale: string;
  status: string;
};

export type GoalRun = {
  run_id: number;
  status: "running" | "awaiting_user" | "ok" | "error" | "expired";
  outcome: string | null;  // no_surplus | nothing_offered | no_affordable_goals when a run ends without goals
  reason: string | null;   // why an error run failed: a code, or an exception name
  goal_ids: number[];
  waiting_for: number[];
  attempts: number | null;
  issues: { message: string; index: number | null; kind: string | null }[];
  error: string | null;
  surplus_source: "forecast" | "measured" | null;
};

export type ForecastDay = { date: string; expected_cents: number; low_cents: number; high_cents: number };
export type ForecastEvent = { date: string; label: string; cents: number; kind: "bill" | "card_min" | "income" | "goal" };
export type Forecast = {
  today: string; status: "ok" | "no_checking" | "no_history"; start_cents: number | null; buffer_cents: number;
  horizon_days: number; income: "paycheck" | "none"; days: ForecastDay[]; events: ForecastEvent[];
  dip: { date: string; days_ahead: number; shortfall_cents: number; bill_label: string | null } | null;
  monthly_surplus_cents: number | null; surplus_source: "forecast" | "measured"; suggested_extra_cents: number | null;
  notes: string[];
};

export function getForecast(days?: number): Promise<Forecast> {
  return request<Forecast>(`/forecast${days === undefined ? "" : `?days=${days}`}`);
}

export function setForecastBuffer(cents: number): Promise<{ buffer_cents: number; default_cents: number }> {
  return request("/forecast/buffer", { method: "PUT", body: JSON.stringify({ buffer_cents: cents }) });
}

// current is the proposal plus the accepted goals; the server's default is the proposal alone
export function getGoals(status?: "proposed" | "accepted" | "current"): Promise<Goal[]> {
  return request<Goal[]>(status ? `/goals?status=${status}` : "/goals");
}

// starts a run in the background (it can outlast the proxy's 30 s); poll getGoalRun with the id
export function generateGoals(date?: string): Promise<{ run_id: number | null }> {
  return request("/goals/generate", { method: "POST", body: JSON.stringify(date ? { date } : {}) });
}

export function getGoalRun(id: number): Promise<GoalRun> {
  return request<GoalRun>(`/goals/runs/${id}`);
}

export function acceptGoal(id: number): Promise<Goal> {
  return request<Goal>(`/goals/${id}/accept`, { method: "POST" });
}

// stops an accepted goal too
export function dismissGoal(id: number): Promise<Goal> {
  return request<Goal>(`/goals/${id}/dismiss`, { method: "POST" });
}

// the transactions table (tpc-22): each row with the budget line it counts against, and corrections
export type Txn = {
  id: number;
  date: string;
  merchant: string | null;
  amount_cents: number;
  category: string;
  line_name: string | null; // null: counts against no line (income, transfers)
  source: "plaid" | "rule" | "user";
};

export function getTransactions(month?: string): Promise<Txn[]> {
  return request<Txn[]>(month ? `/transactions?month=${month}` : "/transactions");
}

// remember: also apply this category to the merchant's other transactions, past and future
export function correctTransaction(id: number, category: string, remember: boolean): Promise<Txn> {
  return request<Txn>(`/transactions/${id}`, { method: "PATCH", body: JSON.stringify({ category, remember }) });
}

// simulated users (tpc-21, admin only): a synthetic user's simulated date, and jobs that advance it

export type SimState = { simulated: boolean; profile: string | null; date: string | null };

export type SimJob = {
  job_id: number;
  status: "running" | "ok" | "error";
  days_requested: number;
  days_done: number;
  start_date: string | null;
  date: string | null;
  run_ids: number[];
  alerts_written: number;
  error: string | null;
};

// 404 when the server's admin routes are off; the alerts page then shows no simulation panel
export type View = "demo" | "plaid";
export type ViewState = { view: View; available: boolean };

// the demo user, or the user whose banks are linked through plaid (admin tools only; a cookie the backend sets)
export function getView(): Promise<ViewState> {
  return request<ViewState>("/admin/view");
}

export function setView(view: View): Promise<ViewState> {
  return request<ViewState>("/admin/view", { method: "PUT", body: JSON.stringify({ view }) });
}

export function getSim(): Promise<SimState> {
  return request<SimState>("/admin/sandbox");
}

export function advanceSim(days: number): Promise<{ job_id: number }> {
  return request("/admin/sandbox/advance", { method: "POST", body: JSON.stringify({ days }) });
}

export function getSimJob(jobId: number): Promise<SimJob> {
  return request<SimJob>(`/admin/sandbox/advance/${jobId}`);
}

export type ChatSource = { title: string; section: string; url: string };
// started_at is epoch ms and ms how long the tool ran; both are missing on calls from before they were recorded
export type ChatTool = { name: string; args: Record<string, unknown>; ok: boolean; started_at?: number; ms?: number };

export type ChatEvent =
  | { event: "sources"; data: { sources: ChatSource[] } }
  | { event: "graph"; data: { used: boolean } }
  | { event: "tool_call"; data: { id: string; name: string; args: Record<string, unknown>; at?: number } }
  | { event: "tool_result"; data: { id: string; name: string; ok: boolean; started_at?: number; ms?: number } }
  | { event: "token"; data: { text: string } }
  | { event: "done"; data: Record<string, never> }
  | { event: "error"; data: { message: string } };

export type ChatTurn = {
  role: "user" | "assistant";
  text: string;
  tools: ChatTool[];
  sources?: ChatSource[]; // assistant turns only
  graph?: boolean; // assistant turns only: the answer used graph facts
};

// one chat turn: posts the message, then calls onEvent for each server-sent event as it arrives.
// a 409 means a reply is already streaming for this user
export async function streamChat(
  threadId: string,
  message: string,
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ thread_id: threadId, message }),
    signal,
  });
  if (!res.ok || !res.body) throw new ApiError(res.status, await readBody(res));
  const reader = res.body.getReader();
  // stream: true keeps a multi-byte character that's split across reads from turning into "�"
  const decoder = new TextDecoder();
  let buffer = "";
  let ended = false;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    const parsed = parseSSE(buffer + decoder.decode(value, { stream: true }));
    buffer = parsed.rest;
    parsed.events.forEach((e) => {
      ended ||= e.event === "done" || e.event === "error";
      onEvent(e as ChatEvent);
    });
  }
  // the server always ends a turn with done or error; a body that just stops was cut off (backend died, proxy dropped it)
  if (!ended) throw new Error("the reply stream ended before the answer finished");
}

export function getChatHistory(threadId: string): Promise<ChatTurn[]> {
  return request<ChatTurn[]>(`/chat/${threadId}`);
}
