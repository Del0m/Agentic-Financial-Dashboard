# Budget ring: manual verification

The dashboard's Budget card is now a ring (a donut chart) instead of one pace bar per line. Each slice is one budget line's share of the month's plan. The centre shows the total spent against the whole plan. The list beside the ring gives each line's spending in words. Accepted goals that start next month are listed under the ring until they join the budget.

## What changed

| File | Change |
|---|---|
| `frontend/components/BudgetPie.tsx` | New. The ring is hand-drawn SVG, one stroked circle per slice. Hovering a slice or focusing a row names it in the centre and highlights its row. Includes `lineStatus` (left, spending fast or over) and `slicesOf` (seven colours, then a grey "Other lines" slice). |
| `frontend/components/BudgetPie.tsx` (goals) | `upcomingGoals` returns accepted goals whose start date is after this period. They're listed under the ring with their monthly amount and start month, and get no slice. The dashboard passes the goals it already loads. |
| `frontend/components/BudgetPaceBar.tsx` and its test | Removed. Nothing else used them. |
| `frontend/app/dashboard/page.tsx` | The Budget card renders `BudgetPie`. Its intro sentence explains the ring and the list. |
| `frontend/app/globals.css` | Slice colours `--slice-1` to `--slice-7` and `--slice-other`, for light and dark. They pass the dataviz palette validator's colour-blind checks on both backgrounds. Red is left out, because red means "over" in the text. |
| Docs | `docs/tests/BudgetPie.test.md` replaces `BudgetPaceBar.test.md`. Updated: the dashboard and alerts page test docs, `architecture.md`, one README line, and the local demo guide. |

What's lost: the bars' day mark. "Spending fast" in the list now carries the pace warning.

## Check it

1. Open http://localhost:3000/dashboard. The Budget card shows a ring beside the list. For the demo user on Oct 1, the slices are Personal care (a thin blue sliver), Rent and utilities, Savings and Everything else. The centre reads "$1,500.00 spent of $4,200.00".
2. Hover each slice. The centre switches to that line's name, amount and percent (Rent is 36%). The other slices fade, and the line's row is highlighted. Move off and the total comes back.
3. Press Tab until a row in the list is focused. The ring responds the same way.
4. Run `laptop` and a budget check:

   ```bash
   docker compose exec backend python scripts/demo_purchase.py laptop --run
   ```

   Refresh the dashboard. The centre total goes up by $1,200, and Everything else reads "... left · spending fast" in amber. The slice sizes don't change, because they show the plan, not the spending.
5. After **Advance 7 days** (Oct 8), Personal care reads "$48.00 over" in red.
6. Switch the system to dark mode. The slices change to the dark-mode colours, and the text stays readable.
7. Generate goals on **Goals** and accept an emergency fund or savings goal. Back on the dashboard, under the ring: "Accepted goals join your budget when their month starts:" and the goal with "$…/mo from November 2026". It becomes a slice after the month closes. Click **Stop** on the goal afterwards to put the demo back.
8. At phone width (375 px), the ring sits above the list, amounts that wrap are right-aligned, and the page doesn't scroll sideways.

## Tests

- Frontend: 159 tests pass (`npm test` in `frontend/`). The 12 bar tests were replaced by 15 for the ring, including 3 for upcoming goals, and the dashboard page tests gained one for passing goals to the card. ESLint and `tsc` are clean.
- In the browser, on the live app: the ring, hovering and fading worked in dark mode at desktop width, in light mode at 375 px (no horizontal scroll), and there were no console errors. Steps 4, 5 and 7 were not run, so the demo state wasn't changed. The upcoming-goal note was checked with a faked goals response in the page only: it appears under the ring with "$1,704.00/mo from November 2026".

## Limits

- Colours follow line order, so adding or removing a line can shift the colours after it.
- With 1% slices like Personal care's $42, the slice is a sliver. The list and the hover still name it.
