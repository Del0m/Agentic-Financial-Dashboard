# Tests: `backend/tests/test_payoff.py`

These tests cover `app/budget_engine/payoff.py`, the debt-payoff planner (TPC-32, architecture section 6a.3). It's a pure function: debts in, a month-by-month plan out, in integer cents. Every month pays one budget, the extra plus every included debt's minimum. Minimums are paid first, and the rest goes to the strategy's target, spilling to the next debt.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_payoff.py -v
```

## Shared setup

- `D(key, apr, balance, minimum, kind, name)` builds a `Debt` from dollars. A `minimum` of `None` means no minimum on file.
- `START` is 2026-12-01, the first planned month.

---

## `test_hand_computed_avalanche`

**What it does:** Debt A is $150 at 12% with a $100 minimum, debt B is $300 at 24% with a $50 minimum, and the extra is $100, so the budget is $250 a month.
- Month 1: interest is $1.50 on A and $6.00 on B. After the minimums, A owes $51.50 and B $256.00. The $100 goes to B (higher APR), leaving $156.00.
- Month 2: interest is $0.52 on A (0.515, rounded half up) and $3.12 on B. A's $100 minimum clears its $52.02. The remaining $147.98 clears B's $109.12.

Both are paid off in month 2 (January 2027), A first. Interest is $2.02 on A and $9.12 on B, $11.14 in all.

**Why it's there:** One case checked to the cent pins the interest formula, the rounding, the order of minimums and extra, and how payoff order is recorded within a month.

## `test_hand_computed_snowball`

**What it does:** The same debts, snowball.
- Month 1: after the minimums, A ($51.50) is the smaller balance. It gets $51.50 of the $100 and is paid off, and the other $48.50 brings B to $207.50.
- Month 2: B's interest is $4.15, bringing it to $211.65. Its minimum leaves $161.65, and the pool of $200 clears it.

Interest is $1.50 on A and $10.15 on B, $11.65 in all.

**Why it's there:** The same check for the other strategy, and it shows avalanche costing less interest on the same debts.

## `test_strategies_target_different_debts`

**What it does:** A $3,200 card at 24.99% and a $900 loan at 5.5%, with $200 extra. Avalanche pays the card off first; snowball pays the loan off first.

**Why it's there:** The two strategies' definitions: highest APR first versus smallest balance first.

## `test_rollover_speeds_up_the_next_debt`

**What it does:** A $2,000 loan on its $60 minimum alone, compared with the same loan after a $500 card that clears early. With the card, the loan is paid off sooner.

**Why it's there:** A paid-off debt's minimum rolls into the next debt. That's what makes both methods "snowball".

## `test_spill_over_in_the_clearing_month`

**What it does:** A $40 card at 24% (minimum $25) and a $1,000 loan at 6% (minimum $50), with $200 extra. In month 1 the pool clears the card's remaining $15.80, and the other $184.20 reaches the loan that month. The loan is paid off in month 4. Without the spill-over it would be month 5.

**Why it's there:** Money left after clearing a debt must not be lost in the month the debt clears.

## `test_never_names_the_debt`

**What it does:** $10,000 at 30% is $250 of interest a month against a $100 minimum. With no extra the plan stops at 600 months: `months` and `debt_free` are `None`, and `never` names the debt. With $300 extra it gets paid off.

**Why it's there:** A minimum that doesn't cover the interest never pays the debt off. The plan has to say so and name the debt, not loop forever or report a date.

## `test_excluded_with_reasons`

**What it does:** A debt with no APR, one with no minimum and a mortgage are listed in `excluded`, in input order, with `no_apr`, `no_minimum` and `mortgage`. Only the fourth debt is planned. With `include_mortgage`, the mortgage is planned too.

**Why it's there:** The planner never invents a rate or a minimum (spec decision), and mortgages are left out unless asked for (section 6a.3).

## `test_zero_apr_is_a_rate`

**What it does:** A $300 promo card at 0% APR with a $100 minimum is planned, not excluded. It's paid off in 3 months with $0 interest.

**Why it's there:** 0% is a real rate. A falsy check would mistake it for a missing APR and drop the card.

## `test_credit_balance_left_out_silently`

**What it does:** A card with a −$50 balance (overpaid) is left out without an excluded entry; the other card is planned.

**Why it's there:** There's nothing to pay off, and a negative balance must not earn negative interest or count as excluded.

## `test_extra_clears_everything_in_month_one`

**What it does:** With $1,000 extra against $500 of debt, everything is paid off in month 1.

**Why it's there:** An extra larger than everything owed ends the plan at once, with nothing carried forward.

## `test_no_debts_and_all_excluded`

**What it does:** No debts gives `months == 0`. A user whose only debt has no APR gets `months == 0`, an empty order, and the debt in `excluded`.

**Why it's there:** These users must get a plan that says nothing is planned, and why, not an error.

## `test_minimum_capped_at_balance`

**What it does:** A $30 debt at 12% with a $100 minimum is paid off in month 1 with $0.30 of interest.

**Why it's there:** A minimum is capped at what's owed. The unused part goes back to the pool instead of overpaying.

## `test_compare_baselines_and_extra_never_hurts`

**What it does:** `compare` returns each strategy with the extra, equal to `simulate`, and each baseline with $0 extra. With $200 extra, neither strategy takes longer or costs more interest than its baseline.

**Why it's there:** The API, the gateway and the dashboard all report "what the extra changes" from these baselines.

## `test_nothing_owed_never_excluded`

**What it does:** A $0 card with no APR, an overpaid card with neither APR nor minimum, and a paid-off mortgage are skipped without an excluded entry. Only the $800 card is planned.

**Why it's there:** Found in the final review. Exclusion used to be checked before the balance, so a paid-off debt with a missing detail showed as "left out of the plan" on the card and in chat.
