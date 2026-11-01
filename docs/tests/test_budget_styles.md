# Tests: `backend/tests/test_budget_styles.py`

These tests cover `app/budget_engine/styles.py` (TPC-22), which fits a budget's lines, total and free money together once each line's limit is known. Init and period close both call `apply_style`.
- **`caps` (default):** the total is typical income (or, with no income history, past spending); free is what the lines leave.
- **`50_30_20`:** income is split into needs, wants and saving targets; the saving line is its target, and free is the room left in wants.

In both, free is never below $0. When the lines don't fit, the total rises and a fact is returned for an alert; no line is cut.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_budget_styles.py -v
```

## Shared setup

- Lines: rent $1,500 (fixed, needs), food $600 (needs), entertainment $200 (wants), savings $500 (fixed, saving).
- `caps(lines, income, fallback)` and `split(lines, income, targets, fallback)` call `apply_style` with the default targets unless given.

---

## `test_caps_free_is_income_minus_lines`

**What it does:** With $4,200 income and $2,800 of lines, free is $1,400 and the total is $4,200, with no facts.

**Why it's there:** The core rule the user asked for: free is the wiggle room in net income.

## `test_caps_free_bottoms_out_and_total_rises`

**What it does:** Rent of $3,200 brings the lines to $4,500 against $4,200 income. Free is $0, the total rises to $4,500, rent is unchanged, and one `over_income` fact carries $4,500 against $4,200.

**Why it's there:** Free bottoms out instead of going negative, and the code reports the gap instead of cutting a line the user didn't ask to cut.

## `test_caps_without_income_uses_fallback`

**What it does:** With no income, a $2,300 spending fallback gives free $200 and a $2,300 total. Even when the lines exceed the fallback, no `over_income` fact is raised.

**Why it's there:** Users without paychecks still get a total. "More than your typical income" would be false when income is unknown.

## `test_503020_targets`

**What it does:** With $4,000 income and needs under 50%, the saving line becomes $800 (20%, not the fitted $500), free is $1,200 − $200 = $1,000, and the total is $4,000.

**Why it's there:** The 50/30/20 split, with free as the room left inside the wants slice.

## `test_503020_creates_saving_line`

**What it does:** A budget with no saving line gets one at 20% of income.

**Why it's there:** The style always has a saving target, even for a user whose history shows no saving.

## `test_503020_group_over_target`

**What it does:** Needs of $2,300 against a $2,000 target give one `group_over_target` fact for needs at 50%, and free is still computed from wants.

**Why it's there:** A group over its target is reported, not trimmed.

## `test_503020_custom_targets`

**What it does:** Targets of 60/25/15 give a $600 saving line and $800 of free money, with no facts.

**Why it's there:** The percentages are a setting, not constants.

## `test_503020_without_income_falls_back`

**What it does:** With no income, 50/30/20 gives exactly the `caps` numbers, with `fallback == "no_income"`.

**Why it's there:** Percentages of an unknown income mean nothing; the close summary says the style fell back.
