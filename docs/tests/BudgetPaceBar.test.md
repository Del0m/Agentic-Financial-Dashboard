# Tests: `frontend/components/BudgetPaceBar.test.tsx`

These tests cover `components/BudgetPaceBar.tsx`, one budget line on the dashboard (TPC-20). The bar fills with the share of the limit spent. A thin marker shows how much of the month has passed, so a fill past the marker means spending is ahead of pace. Amber means the agent flagged it for pace, and red means it's over the limit. The text beside the amounts says the same in words ("$X left", "spending fast", "$X over"), so colour is never the only signal.

Run them from `frontend/`:

```bash
npm test -- components/BudgetPaceBar.test.tsx
```

## Shared setup

- `line(over)` builds a food line: a $472 limit with $236 spent, unflagged.
- `draw(line)` renders it on day 20 of 31 and returns the meter, the fill and the today marker.

---

## `fills by spent over limit and marks today`

**What it does:** $236 of $472 gives a meter value of 50 and a 50% fill. The today marker sits at 20/31 of the width. The label reads "Food and drink" and "$236.00 of $472.00".

**Why it's there:** The marker against the fill is how the user reads pace at a glance, and `role="meter"` makes the same number available to screen readers.

## `amber on pace, red over the limit, neutral otherwise`

**What it does:** The fill is amber for a pace flag, red for over the limit, and neither for an unflagged line.

**Why it's there:** The colours match the alert severities ("Ahead of pace", "Over limit") the user sees on the Alerts page.

## `caps the bar at 100% but shows what was really spent`

**What it does:** $705.31 against a $149.96 limit fills 100% (meter 100), and the text still says "$705.31 of $149.96".

**Why it's there:** A bar can't grow past its box, and the user still needs the real amount.

## `a zero limit draws an empty bar`

**What it does:** A limit of $0 gives a 0% fill instead of dividing by zero.

**Why it's there:** A zero limit can happen, and the dashboard mustn't show `NaN%` or crash.

## `fixed lines say so`

**What it does:** A fixed line (rent) is labelled "(fixed)".

**Why it's there:** Fixed lines aren't judged on pace (rent lands on the 1st), so a full bar early in the month is expected.

## `shows the line's name`

**What it does:** A line named "Groceries" is labelled "Groceries", and its meter is "Groceries spent".

**Why it's there:** Lines carry the user's name (TPC-22), not Plaid's raw category key; the reserved keys show as "Savings" and "Everything else".

## `a goal line shows its monthly amount and no meter`

**What it does:** A line in the `goal` group (an accepted "Debt payoff" goal, $583.00) shows its name and "$583.00/mo". It has no meter, and no "$0.00 of $583.00".

**Why it's there:** Nothing reliably measures what was paid toward a goal: card payments aren't spending, and a transfer can't be tied to a goal. A pace bar would sit at $0 all month and read as a failure (TPC-25).

## `says how much is left instead of a pace number`

**What it does:** $236 of $472 reads "$236.00 left", and no "pace" number appears.

**Why it's there:** A raw pace ratio such as "pace 8.30" meant nothing to a reader without the docs. The amount left answers the question they actually have.

## `ahead of pace says it is spending fast`

**What it does:** A line flagged for pace, $400 of $472, reads "$72.00 left · spending fast".

**Why it's there:** It names the amber state in words.

## `over the limit says by how much`

**What it does:** $90 against $42 reads "$48.00 over".

**Why it's there:** It names the red state in words and gives the amount, which the bar can't show past 100%.

## `fixed and saving lines show no left or over`

**What it does:** A fixed rent line and a saving line show neither "left" nor "over".

**Why it's there:** A fixed bill is paid once, so "left" means nothing there, and saving can't be overspent. An empty saving line would otherwise read as money left to spend.

## `the reserved free line reads as everything else, a renamed one keeps its name`

**What it does:** The reserved `FREE` line named "Free" shows as "Everything else", in its label and its meter name. Renamed to "Fun money", it keeps the user's name.

**Why it's there:** "Free" read like free money. The line actually holds spending with no line of its own.
