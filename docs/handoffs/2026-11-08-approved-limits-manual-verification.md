# Approved limits on the Budget card: manual verification

Accepting a budget increase (for example, the agent's proposal to raise Personal care from $42 to $135) records it for **next month**. Month close applies it. Until then, the Budget card showed nothing, so Accept looked like it had done nothing. Goals already had an "Accepted goals join your budget…" note; limit changes didn't.

The card now lists approved changes under the ring, for example "Changes you approved, starting November 2026: Personal care · $42.00 now → $135.00 next month". This month's row and slice keep the current limit.

## What changed

| File | Change |
|---|---|
| `backend/app/budget_service/close.py` | New `accepted_limits(session, period)`: the limits approved for the next month. It covers accepted increases (the latest answer wins), then accepted subscription cancellations, which take the stream's cost off. `gather_month` now calls it, so close's behaviour is unchanged. |
| `backend/app/api/budget.py` | `GET /budget` returns `next_limits` (category, name, current limit, next limit) and `next_start`. It uses the same helper as close, so the dashboard lists exactly what close will apply. Free and goal lines are left out, because close never adjusts them from an approval. |
| `frontend/lib/api.ts` | `Budget.next_limits` and `next_start`. |
| `frontend/components/BudgetPie.tsx` | The note under the ring now has two parts: approved changes ("$X now → $Y next month") and the existing upcoming goals. It appears only when there's something to list. |
| Docs | `test_budget_api.md`, `BudgetPie.test.md` and `architecture.md` (Budget card). |

## Check it

1. As **Demo user**, open Alerts and **Accept** the Personal care proposal ($135 for next month). After a fresh reset, it appears after **Advance 7 days**.
2. Open the Dashboard. Under the ring: "Changes you approved, starting November 2026:" and "Personal care · $42.00 now → $135.00 next month".
3. Personal care's row still reads "$90.00 of $42.00 · $48.00 over", and its slice hasn't grown. October's limit didn't change.
4. Optional: reset and leave the proposal pending, or click Dismiss. The note doesn't appear.
5. Optional, outside the timed demo: run the simulation to November 1. Personal care's limit becomes $135, and the note disappears, because November's period has no approvals yet.

## Tests

- Backend: 1,991 offline tests pass. The new ones: accepted increase listed with the old limit kept for this month; pending and dismissed proposals list nothing.
- Frontend: 172 tests pass. The new ones: the note's wording with October's row unchanged, and no note when nothing is approved. ESLint and `tsc` are clean.
- Live app: the demo user had an accepted Personal care proposal (alert 15). `GET /budget` returned `$42 → $135` from 2026-11-01. The dashboard showed the note under the ring with Personal care still "$48.00 over". No console errors. I didn't check phone width or dark mode. The note uses the same layout as the goals note, which was checked in both.

## Limits

- Close applies the approved amount as is, under either style. One exception: with 50/30/20 and known income, close sets the Savings line from the 20% target, so an approved change to Savings would be listed but overridden. The agent doesn't propose changes to Savings in the demo.
- A cancellation accepted for a line with no budget line isn't listed, because close has nothing to apply it to.
