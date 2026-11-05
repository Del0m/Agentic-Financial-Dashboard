# Dashboard polish: how to check it by hand

Written 2026-11-05. The work is on branch `dashboard-polish`, in the worktree `.claude/worktrees/dashboard-polish`, cut from `635b529`. Nothing is committed yet. The main checkout stays on `TPC-27-benchmark` at `635b529`, so the benchmark can restart there without redoing finished conditions.

## What changed

Frontend and README only. No backend or agent code changed, so benchmark results still describe the agents in this branch.

| Problem in the demo | Change |
|---|---|
| The dashboard opened with a red "Couldn't start Link: Bank linking is disabled for simulated users" | The Link button only appears once the page knows the user is real. A simulated user gets a "Demo · Oct 8, 2026" chip instead |
| "pace 8.30" meant nothing to a reader | Flexible lines say "$X left", "$X left · spending fast" or "$X over". Fixed and saving lines show only the amounts |
| The today marker on each bar was unexplained | The Budget card starts with "Day 8 of 31. The mark on each bar shows how much of the month has passed…" |
| The reserved "Free" line read like free money | It shows as "Everything else", on the bar and in the transaction picker. A renamed free line keeps the user's name |
| "Your typical range is between $46.43 and $100.00 on Oct 14" | "Your lowest point is likely Oct 14, with a balance between $46.43 and $100.00." |
| The README's demo walkthrough promised one alert in 30 seconds | It says the 7-day advance takes 2 to 3 minutes, and that low-balance warnings appear beside the personal care alert |

## Not changed

These are agent or seed-data behaviour, so they should be described in the paper as they are, not fixed before submission:

- Low-balance alerts repeat, two refer to dates already passed, they show raw ISO dates, and each has an **Accept** button whose meaning isn't clear. One cites a $500 Landlord bill against $1,500 rent.
- The demo's checking balance stays at $100 while spending goes through, so net worth doesn't move as the month advances.

## Automated checks

From the worktree's `frontend/`:

```bash
npx vitest run
```

145 tests pass, 8 of them new.

```bash
npm run lint
```

Clean. `npx tsc --noEmit` fails in the worktree with `Cannot find name 'LayoutProps'` in `app/layout.tsx`, because that type is generated into `.next/` by `next dev`. The same command passes in the main checkout.

## Check it in the browser

The Docker demo runs the main checkout's code, so it won't show these changes until the branch is merged. To see them now, with Docker running, start this branch's frontend on port 3001 from the worktree's `frontend/`:

```bash
npm run dev -- --port 3001
```

It talks to the Docker backend on port 8000. Open http://localhost:3001/dashboard.

1. **No red error.** The header shows Alerts, Goals, Chat and a "Demo · <date>" chip, with no Link button and no error.
2. **Budget card.** It opens with "Day N of M…". Personal care reads "$90.00 of $42.00 · $48.00 over" in red. Rent and Savings show only their amounts. The last line is "Everything else … $X left".
3. **Cash forecast.** The sentence starts "Your lowest point is likely".
4. **Transaction picker.** In Transactions, open a line picker. It lists "Everything else", not "Free".
5. **Phone width.** In a narrow window the amounts wrap under the line name, and nothing scrolls sideways.
6. **A real user.** A user that isn't simulated still gets "Link another account". The automated test covers this, because the Docker demo user is always simulated.

## Demo state

To see the budget bars, the Docker demo was reset with the README's restart commands and advanced to October 8. It has four pending alerts, which matches step 1 of the walkthrough.
