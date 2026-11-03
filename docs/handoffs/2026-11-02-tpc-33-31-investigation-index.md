# TPC-33 and TPC-31: investigation index

Date: 2026-11-02. Four investigations ran on synthetic users only, then fed one design and two plans. Nothing is implemented.

## What was found

- **TPC-33 was never built.** The architecture (6a.1) describes a recurring-charge detector, but no code, table or endpoint exists. `Transaction.is_recurring` is a dead column, and `alerts.stream_id` is not there. The docs also say `days_requested` was raised to 180; the code still sends 90 (`backend/app/plaid_service/client.py:50,65`).
- **Four of the tickets' methods fail on the data.** A price rise over 10% can never alert; the 7/14/15-day classes turn random merchants into streams (11 of 17 on a noisy profile); the median day of everyday spending is $0, so "median x 30" is off by 100%; "balance at the next paycheck" as surplus gives $9,353 to $11,024 against a measured $971.75. The design replaces each, with numbers, in its "Departures from the tickets" table.
- **Some rules are unmeasured** (short-gap streams, the monthly next date, and the skip, restart and price-step rules added in review). The TPC-33 plan stops at a gate after Task 2 until they meet stated numbers.
- **Synthetic users cannot be forecast** (a static $100 balance, no biweekly pay, no price change). Both plans share one seeding change in TPC-33 Task 1.

## Documents

- [Investigation 1: recurring streams](2026-11-02-tpc-33-31-investigation-1-recurring-streams.md)
- [Investigation 2: forecast and surplus](2026-11-02-tpc-33-31-investigation-2-forecast-and-surplus.md)
- [Investigation 3: agent integration](2026-11-02-tpc-33-31-investigation-3-agent-integration.md)
- [Investigation 4: API, UI and eval](2026-11-02-tpc-33-31-investigation-4-api-ui-eval.md)
- [Design spec](../superpowers/specs/2026-11-02-tpc-33-31-recurring-and-forecast-design.md) (read its departures table first)
- [TPC-33 plan](../superpowers/plans/2026-11-02-tpc-33-recurring-streams.md), built first on branch `TPC-33`
- [TPC-31 plan](../superpowers/plans/2026-11-02-tpc-31-cash-flow-forecast.md), built second on branch `TPC-31` from `TPC-33`

## How TPC-33 came to be marked Done

Jira closed TPC-33 automatically seven seconds after PRs #9, #11 and #12 had merged. Each of those PRs mentions TPC-33; none implements it. The ticket has been reopened. The transition came from the mention, not from the work. I did not read the Jira automation rule, so its exact trigger (PR text, branch name or commit message) is unverified.

## The same risk for TPC-31

PR #23 (TPC-25, goal approval) has TPC-31 in its description. When #23 merges, Jira may close TPC-31 the same way. Before merging #23, remove the key from the description (write "the cash-flow forecast" instead) or check the automation rule. Both plans carry a Global Constraint for this: a PR names only the ticket it completes, and the TPC-33 PR does not name TPC-31 (nor the reverse). After any merge, check the board.
