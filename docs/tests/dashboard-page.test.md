# Tests: `frontend/app/dashboard/page.test.tsx`

These tests cover the goal parts of the dashboard, `app/dashboard/page.tsx` (TPC-25): the Goals card and the Goals link in the header.

Run them from `frontend/`:

```bash
npm test -- app/dashboard/page.test.tsx
```

## Shared setup

- `@/lib/api` is mocked with `vi.mock`: `getDashboard`, `getBudget`, `getAlerts`, `getTransactions`, `getGoals`, `getRecurring`, `getForecast` and `getSim`. `getSim` fails with a 404 by default, as it does when the admin routes are off, so the user is a real one.
- `BalanceChart`, `DebtPlanCompare` and `ForecastChart` are replaced with empty components, and `PlaidLinkButton` with a plain button showing its label. They draw charts, open Plaid and call the planner, none of which is under test.
- The user has one linked account and a December budget with a "Debt payoff" goal line.
- `goal(id, over)` builds an accepted goal.

---

## `accepted goals appear in a Goals card with their start month`

**What it does:** Two accepted goals show their names, amounts and "From December 2026" and "From January 2027". Goals are loaded with `"current"`.

**Why it's there:** The ticket's done-when is that accepting a goal shows it on the dashboard. The start month tells the user which budget it joins.

## `proposed goals aren't listed in the card`

**What it does:** With only a proposed goal, the card says "No active goals right now."

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

## `shows the Recurring charges card`

**What it does:** Renders the independent recurring card.

**Why it's there:** Covers the recurring integration without coupling it to other cards.

## `keeps a recurring failure inside its card`

**What it does:** Retains Goals and Transactions when the recurring request fails.

**Why it's there:** Covers the recurring integration without coupling it to other cards.

## `shows the empty text for a user with no streams`

**What it does:** Shows the recurring empty-state copy.

**Why it's there:** Covers the recurring integration without coupling it to other cards.

## `shows the Cash forecast card`

**What it does:** Shows the cash forecast card.

**Why it's there:** Checks the forecast integration while preserving the existing workflow.

## `keeps a forecast failure inside its card`

**What it does:** Keeps a forecast failure inside its card.

**Why it's there:** Checks the forecast integration while preserving the existing workflow.

## `a real user can link another account`

**What it does:** An existing bank shows "Bank connected" with no Connect/Link another account button. An unlinked user sees the Connect a bank prompt. A connected bank with no accounts yet offers Retry sync rather than another Link flow.

**Why it's there:** A real user must keep the Link button, including when the admin routes are off.

## `a simulated user gets no Link button, and sees its simulated date`

**What it does:** For a simulated user on Oct 8, 2026, the header shows "Demo · Oct 8, 2026" and no Link button.

**Why it's there:** The server refuses bank linking for simulated users. The button requested a link token as soon as it appeared, so the demo opened with a red "Couldn't start Link" error. The chip also says which date the demo is on.

## `the budget card lists an accepted goal that starts after this month`

**What it does:** With December's budget and an accepted savings goal starting in January 2027, the Budget card shows "$200.00/mo from January 2027".

**Why it's there:** It checks that the dashboard passes its accepted goals to the Budget card, so a goal the user just accepted shows up there too.

## `the budget card explains the ring and the list`

**What it does:** With day 10 of 31, the Budget card starts with "Day 10 of 31. The ring shows how your monthly budget is split…", and the ring is there as an image named "Budget split…".

**Why it's there:** The ring shows the plan and the list shows the spending. The sentence tells the reader which is which, so they don't read a slice's size as money spent.

## User-switch regression

`does not expose a user switch` checks that the page has no “Whose data to show” group, “Demo user” button or “Plaid bank” button. The application uses its server-configured user instead.

## Fresh-page connection state

`a fresh page mount waits for saved connection state before offering Link` unmounts the dashboard and mounts it again with a delayed connection response. Neither Connect a bank nor Link another account is rendered while waiting; the saved connection then restores Bank connected. The dashboard fetch explicitly uses `cache: no-store`.

## Sandbox disconnect confirmation

The Dashboard exposes Disconnect bank only when `can_disconnect` is true. Opening/cancelling confirmation does not call the endpoint. Confirmed success clears stale financial state and reloads the server's unlinked Dashboard, while a failed request leaves the bank connected and displays its error. No test clicks through a real live disconnect.
