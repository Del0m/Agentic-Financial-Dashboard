# Tests: `frontend/components/GoalCard.test.tsx`

These tests cover `components/GoalCard.tsx`, one goal on the Goals page (TPC-25). A proposed goal shows its monthly amount, target, rationale and the month it would join the budget, with Accept and Dismiss. An accepted goal says when it joins the budget and offers Stop, which ends its budget line at the next close.

Run them from `frontend/`:

```bash
npm test -- components/GoalCard.test.tsx
```

## Shared setup

- `goal(over)` builds a proposed avalanche debt-payoff goal: $583.00/mo, target $21,600.00 by December 2029, starting Dec 1, 2026.
- `draw(goal, busy)` renders it with mock `onAccept` and `onDismiss` callbacks.

---

## `a proposed goal shows its numbers and both decisions`

**What it does:** The card shows "Debt payoff (avalanche)", "$583.00/mo", "Target $21,600.00 by December 2029", and the rationale. The target date is a month, with no day, and "Dec 1" doesn't appear. Accept and Dismiss call back with the goal's id.

**Why it's there:** The user is committing money for years, so every figure behind the decision is on the card. The goal's line runs through its target month, so a day would be a false precision. The page sends the id to `POST /goals/{id}/accept` or `/dismiss`.

## `an accepted goal says when it joins the budget and offers Stop`

**What it does:** An accepted emergency-fund goal reads "In your budget from December 2026", has a Stop button and neither Accept nor Dismiss, and says its budget line ends at the next close. Stop calls `onDismiss` with the id.

**Why it's there:** A goal joins the budget at the next close, never in the month under way, so the user is told which month. Stopping calls the dismiss route, and the line only ends at the next close, which the card says so the click isn't read as instant.

## `buttons are disabled while busy`

**What it does:** While a decision is in flight, Accept, Dismiss and Stop are all disabled.

**Why it's there:** A double click would send a second request that gets `409`.

## `a proposal with no start date can only be dismissed`

**What it does:** A proposed goal whose `start_date` is null has no Accept button, keeps Dismiss, and tells the user to generate again.

**Why it's there:** Rows from before TPC-25 have no start date, and the API refuses to accept them (`409`). The button would only produce an error.

## `a goal with no target date still shows its target`

**What it does:** A savings goal with no `target_date` shows "Savings" and its $1,000.00 target, without a "by" date.

**Why it's there:** The target date is nullable in the API, and the card mustn't print "by —" or "by null".
