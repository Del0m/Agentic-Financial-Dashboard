# Tests: `frontend/components/RecurringList.test.tsx`

Run from `frontend/`: `npm test -- components/RecurringList.test.tsx`.

## Shared setup

Testing Library renders typed recurring responses with explicit November dates. No API is called. Each test cleans up its DOM.

## `shows a monthly subscription with its next date`

**What it does:** Displays the merchant, cadence, typical dollars and next date in UTC.

**Why it's there:** Pins the recurring card's user-visible behavior.

## `prefixes about when the amount varies`

**What it does:** Marks variable charges as approximate.

**Why it's there:** Pins the recurring card's user-visible behavior.

## `badges a price increase with both prices`

**What it does:** Shows the exact old and new prices in the change badge.

**Why it's there:** Pins the recurring card's user-visible behavior.

## `shows an inflow as Income with no employer`

**What it does:** Uses a generic Income label even if an identifying label is supplied.

**Why it's there:** Pins the recurring card's user-visible behavior.

## `groups stopped streams under No longer seen`

**What it does:** Separates stale streams and labels their date Stopped.

**Why it's there:** Pins the recurring card's user-visible behavior.

## `shows the empty text and the too-little-history text`

**What it does:** Explains empty data and null analysis dates.

**Why it's there:** Pins the recurring card's user-visible behavior.

## `keeps price and income badges readable when the previous amount is unavailable`

**What it does:** Retains readable change badges when the detector has cleared the previous amount.

**Why it's there:** Pins the recurring card's user-visible behavior.
