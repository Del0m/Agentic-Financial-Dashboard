# Tests: `frontend/components/DebtPlanCompare.test.tsx`

These tests cover `components/DebtPlanCompare.tsx` (TPC-32), the dashboard card under "Debts by APR" that compares paying off the user's debts with avalanche and with snowball. It takes an "Extra per month" in whole dollars, waits 300 ms after typing stops, and asks `POST /debt-plan`. Every number on it comes from the backend's planner.

Run them from `frontend/`:

```bash
npm test -- components/DebtPlanCompare.test.tsx
```

## Shared setup

- `plan(over)` is a two-month avalanche plan: $11.14 of interest and one payoff (Platinum Card, January 2027).
- `response(extra, over)` is a full `DebtPlan`:
  - avalanche at $11.14 and snowball at $11.65;
  - baselines of 5 months, at $30.00 and $31.00;
  - nothing excluded.
- `draw(fetchPlan)` renders the card with a fake `fetchPlan` under fake timers, and lets the first request (at $0) go out and resolve.
- `typeExtra(value)` types into "Extra per month" and waits out the 300 ms.

---

## `renders both plans and marks the cheaper one`

**What it does:** Both columns show, with $11.14 and $11.65 of total interest. The "saves the most interest" tag is in the Avalanche column.

**Why it's there:** The point of the card is the comparison. The tag goes on the lower-interest strategy, and on neither when they tie.

## `refetches 300 ms after the input settles`

**What it does:** Typing "2", "20" and then "200", less than 300 ms apart, makes one request with 20,000 cents after the $0 one on load.

**Why it's there:** Each keystroke would otherwise be a request, and their answers could arrive out of order.

## `ignores a stale response`

**What it does:** The request for $100 is slow. The one for $300 answers first, with 7 months. When the $100 answer (99 months) arrives afterwards, the card still shows 7 months.

**Why it's there:** It's plan Review Focus 5: typing fast must never leave an older answer on screen under the newer input.

## `shows the difference line only with an extra`

**What it does:** At $0 there's no "months sooner" line. At $200 the avalanche column reads "+$200/mo: 3 months sooner, $18.86 less interest", against its baseline.

**Why it's there:** The difference is what the extra buys. At $0 there's nothing to compare.

## `lists excluded debts and a never plan`

**What it does:** A store card with no APR is listed as "Store Card: no APR on file". When the card is never paid off under either strategy, both columns say "Platinum Card isn't paid off within 50 years at this pace."

**Why it's there:** A plan must say which debts it leaves out and which can't be paid off. Otherwise it reads as an exact answer when it isn't. The never message uses the name the API now returns with each never debt. Such a debt isn't in the payoff order, so it has no name there. The message says only what the plan knows. The first wording, "its minimum doesn't cover its interest", was false for a long loan whose minimum covers the interest but takes more than 600 months (final review).

## `all debts excluded shows only the excluded list`

**What it does:** When every debt is excluded (0 months, empty order), neither column renders and no "debt-free" text appears. Only the excluded list shows.

**Why it's there:** It's plan Review Focus 4: "0 months" would read as "debt-free now" to a user whose debts simply can't be planned.

## `refetches when the dashboard's debts change, keeping the extra`

**What it does:** After "200" is typed, the dashboard re-renders the card with a new `debtsKey` (a second debt appeared). The card asks for the plan again with 20,000 cents, and the input still reads 200.

**Why it's there:** Found in the final review. Linking a bank, a retry or a sync reloads the dashboard's debts under a card that's already on screen. The plan stayed stale, showing one debt while the table showed two, until the user typed again.

## `shows the suggested extra without changing the input`

**What it does:** Shows the suggested extra without changing the input.

**Why it's there:** Checks the forecast integration while preserving the existing workflow.

## Accepted goal contribution display

The component displays accepted debt-goal amounts and dates, plus the first-month minimums/goal/additional-extra breakdown. Tests keep the extra input at zero when a goal is already included, verify typed extra is additive, and refetch when accepted goals change without a balance change. Results from a superseded request are ignored.
