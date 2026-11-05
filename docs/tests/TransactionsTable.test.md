# Tests: `frontend/components/TransactionsTable.test.tsx`

These tests cover `components/TransactionsTable.tsx` (TPC-22), the dashboard's list of this month's transactions. Each row shows the budget line it counts against in a picker. Plaid's category applies on its own; picking another line corrects that one transaction, and an "Always for <merchant>?" button then offers to remember it for every transaction from that merchant.

Run them from `frontend/`:

```bash
npm test -- components/TransactionsTable.test.tsx
```

## Shared setup

- Lines: "Groceries" (`FOOD_AND_DRINK`), "Shopping" (`GENERAL_MERCHANDISE`) and the reserved free line, shown as "Everything else".
- `txn(over)` is a $210 Costco purchase on Oct 12, on Shopping, from Plaid.
- `draw(rows, onCorrect, onRemembered)` renders the table with mock callbacks.

---

## `renders rows with their line`

**What it does:** The Costco row shows its merchant, $210.00 and a picker set to Shopping. A paycheck (no line) has no picker.

**Why it's there:** Income and transfers count against no line, so there's nothing to pick.

## `picking a line corrects the row`

**What it does:** Choosing Groceries calls `onCorrect(1, "FOOD_AND_DRINK", false)`, and the row shows the corrected line.

**Why it's there:** A correction applies to that transaction only, unless the user asks to remember it.

## `offers "Always for Costco?" and remembers`

**What it does:** After the pick, an "Always for Costco?" button appears. Clicking it calls `onCorrect` again with `remember: true`, calls `onRemembered` (so the page reloads the merchant's other rows), and the button goes away.

**Why it's there:** The user's choice: remembering is offered once, never assumed.

## `marks rule rows`

**What it does:** A row whose category came from a merchant rule shows a small "rule" marker.

**Why it's there:** The user can see why a transaction landed where it did.

## `spending without a line shows Free`

**What it does:** A travel purchase (no travel line) shows the picker set to the free line, labelled "Everything else".

**Why it's there:** Spending without its own line counts against the free line.

## `goal lines aren't offered as a category`

**What it does:** With a "Debt payoff" line in the `goal` group among the lines, the picker still lists only Groceries, Shopping and Everything else.

**Why it's there:** A goal line has no spending to hold, and `PATCH /transactions/{id}` refuses it with a 422 (TPC-25). The picker shouldn't offer a choice that can only fail.
