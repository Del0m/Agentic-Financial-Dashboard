# Tests: `frontend/app/dashboard/page.test.tsx`

These tests cover the goal parts of the dashboard, `app/dashboard/page.tsx` (TPC-25): the Goals card and the Goals link in the header.

Run them from `frontend/`:

```bash
npm test -- app/dashboard/page.test.tsx
```

## Shared setup

- `@/lib/api` is mocked with `vi.mock`: `getDashboard`, `getBudget`, `getAlerts`, `getTransactions` and `getGoals`.
- `BalanceChart`, `PlaidLinkButton` and `DebtPlanCompare` are replaced with empty components. They draw a chart, open Plaid and call the planner, none of which is under test.
- The user has one linked account and a December budget with a "Debt payoff" goal line.
- `goal(id, over)` builds an accepted goal.

---

## `accepted goals appear in a Goals card with their start month`

**What it does:** Two accepted goals show their names, amounts and "From December 2026" and "From January 2027". Goals are loaded with `"current"`.

**Why it's there:** The ticket's done-when is that accepting a goal shows it on the dashboard. The start month tells the user which budget it joins.

## `proposed goals aren't listed in the card`

**What it does:** With only a proposed goal, the card says there are no accepted goals.

**Why it's there:** `GET /goals?status=current` returns proposals too. A proposal isn't part of the budget yet.

## `a goals failure stays inside its card`

**What it does:** When `getGoals` fails, the Goals card shows the error, while the budget card and the header link still work.

**Why it's there:** A failure in one part mustn't blank the dashboard, as for the budget card.

## `the header link counts proposed goals`

**What it does:** With two proposed goals and one accepted, the link reads "Goals (2)" and points to `/goals`.

**Why it's there:** It tells the user a proposal is waiting on them, as the Alerts link does.

## `with nothing proposed the link has no count`

**What it does:** With no goals, the link reads "Goals".

**Why it's there:** "Goals (0)" is noise.
