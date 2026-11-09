# Tests: `frontend/components/BudgetPie.test.tsx`

These tests cover `components/BudgetPie.tsx`, the dashboard's Budget card (TPC-20). It replaced a pace bar per line, which was hard to read.

The ring shows the month's plan, with each slice sized by one line's share of the budget. Its centre shows the total spent against the whole plan. Hovering a slice, or focusing a row with the keyboard, puts that line's name, amount and share in the centre, fades the other slices and highlights the row. The list beside the ring gives each line's spending in words: "$X of $Y", then "$X left", "spending fast" in amber, or "$X over" in red. So colour is never the only signal.

An accepted goal joins the budget on its start date, at the month close before it. Until then it's listed under the ring as "Emergency fund · $1,704.00/mo from November 2026", with a dashed swatch and no slice.

A limit the user approved for next month (an accepted budget increase or cancellation) is listed the same way: "Personal care · $42.00 now → $135.00 next month", under "Changes you approved, starting November 2026". This month's row and slice keep the current limit.

The slices use seven colours, given out in line order, with red left out because red means "over" in the text. Past seven lines, the smallest share one grey "Other lines" slice.

Run them from `frontend/`:

```bash
npm test -- components/BudgetPie.test.tsx
```

## Shared setup

- `line(over)` builds a food line: a $472 limit with $236 spent, unflagged.
- `budget(lines)` wraps lines in a budget on day 20 of 31.
- `goal(over)` builds an accepted emergency fund goal, $1,704 a month from Nov 1, 2026.
- `DEMO` is the demo user's plan: Personal care ($42, $90 spent, over), Rent and utilities ($1,500, fixed), Savings ($500) and Everything else ($2,158, $1,200 spent, flagged for pace).

---

## `one slice per line, sized by its share of the plan`

**What it does:** The demo plan draws four slices in line order. Rent's arc is 1,500/4,200 of the ring, less the 2px gap between slices. The ring's accessible name lists "Rent and utilities $1,500.00 (36%)".

**Why it's there:** The slice sizes are the chart. Screen readers get the same split as text.

## `the centre says how much is spent of the whole plan`

**What it does:** The centre reads "$2,790.00" and "spent of $4,200.00".

**Why it's there:** The ring shows the plan, so the headline number shows how far into it the month's spending is.

## `the list says each line's spending in words`

**What it does:** Personal care reads "$90.00 of $42.00" and "$48.00 over" (red). Everything else reads "$958.00 left · spending fast" (amber). The free line is labelled "Everything else", and both fixed lines say "(fixed)".

**Why it's there:** These words replaced what the bar's colour and day mark used to show.

## `an unflagged line says how much is left`

**What it does:** $236 of $472 reads "$236.00 left", and no "pace" number appears.

**Why it's there:** The amount left answers the reader's question, and a raw pace ratio didn't.

## `fixed and saving lines show no left or over`

**What it does:** Rent and Savings show neither "left" nor "over".

**Why it's there:** A fixed bill is paid once, and saving can't be overspent. An empty saving line would otherwise read as money left to spend.

## `hovering a slice names it in the centre, and highlights its row`

**What it does:** Hovering the rent slice replaces the centre with "$1,500.00 · 36%" and highlights the rent row. Moving off puts the total back.

**Why it's there:** The smallest slices (Personal care is 1%) have no room for a label, so hovering is how the reader identifies one.

## `focusing a row does the same from the keyboard`

**What it does:** Focusing the Savings row shows "$500.00 · 12%" in the centre.

**Why it's there:** Keyboard users can't hover.

## `a goal line is a slice of the plan with no spending`

**What it does:** An accepted "Debt payoff" goal ($583) gets a slice and reads "$583.00/mo planned", with no "$0.00 of $583.00".

**Why it's there:** A goal is part of the plan, but nothing measures what was paid toward it, so "$0 spent" would read as a failure (TPC-25).

## `a zero limit gets no slice but stays in the list`

**What it does:** A $0 Travel line draws no slice but is still listed.

**Why it's there:** A zero-size slice would only add a stray gap, and the user still sees the line exists.

## `a single line fills the whole ring`

**What it does:** One line's arc is the full circumference, with no gap taken out.

**Why it's there:** With one slice there's nothing to separate, and a gap would leave a notch in the ring.

## `past seven lines, the smallest share one grey slice`

**What it does:** With nine lines, the six largest get colours 1 to 6, and the three smallest ($10, $20, $30) share an "Other lines" slice in the grey colour.

**Why it's there:** More than about seven colours blur together. Every line is still listed by name.

## `a renamed free line keeps its name`

**What it does:** The free line renamed "Fun money" shows that name, not "Everything else".

**Why it's there:** "Everything else" replaces only the default name "Free", which read like free money.

## `an accepted goal that starts next month is listed under the ring, with no slice`

**What it does:** With October's demo plan and an accepted emergency fund goal starting Nov 1, the card shows "Accepted goals join your budget when their month starts:", then "Emergency fund" and "$1,704.00/mo from November 2026". The ring still has four slices.

**Why it's there:** Before this, a goal accepted mid-month didn't show on the Budget card at all, because it only joins at the next close. That looked like the Accept button had failed.

## `only accepted goals that start after this month count as upcoming`

**What it does:** Of an accepted goal starting in November, one starting in October, a proposal and a goal with no start date, only the November one is upcoming. With no open period, nothing is.

**Why it's there:** A goal starting this month is already a goal line in the ring, so listing it again would count it twice. A proposal isn't in the budget until the user accepts it.

## `no upcoming goals, no extra list`

**What it does:** Without goals, the "Accepted goals join…" note doesn't appear.

**Why it's there:** Most months have no goal waiting, so an empty note would only add clutter.

## `an approved limit is listed for next month, and this month keeps its limit`

**What it does:** With an approved Personal care change from $42 to $135, the card shows "Changes you approved, starting November 2026:" and "$42.00 now → $135.00 next month". October's row still reads "$90.00 of $42.00" and "$48.00 over".

**Why it's there:** The approval is for next month. Without the note, accepting it looked like nothing happened. Changing October's row would wrongly say the limit had already moved.

## `no approved limits, no next-month list`

**What it does:** With an empty `next_limits`, the "Changes you approved" note doesn't appear.

**Why it's there:** Most months have nothing approved, so an empty note would only add clutter.
