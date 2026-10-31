# Tests: `backend/tests/test_m3_demo.py`

This is TPC-22's done-when, the M3 milestone demo: **a simulated month with a planted overspend alerts within one run of crossing 10%, with the right cause, and next month's budget adapts.** It runs the real budget agent and the real period close end to end, through the simulator, with a schema-aware fake model so it needs no Ollama. The live run with `qwen2.5:14b` is recorded in `docs/architecture.md`.

Run it from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_m3_demo.py -v
```

## Shared setup

- The planted `overspend-recurring` user (a $90 gym that started Sep 3, on top of a $40 barber), seeded up to Sep 30.
- `SchemaReplyModel(cause="recurring", first_action="adjust_next_budget")` answers every diagnosis `recurring` and a category's first flag of the month with `adjust_next_budget`; an `InMemorySaver` lets the proposal pause its run, as in the app.
- `first_flagged_day` is the oracle: it calls the budget engine's `evaluate` directly for each October day's transactions and returns the first day the gym's category is flagged (over its limit, or at 110% of its pace).

---

## `test_m3_demo`

**What it does:**
1. Advances 31 days (Oct 1–31). The first personal-care alert comes from a run no later than one day after the oracle's first flagged day (in practice both are Oct 3, when the gym charge lands), with cause `recurring` and action `adjust_next_budget`.
2. Accepts the proposal ($135, computed by code) through `decide_alert`, which resumes the paused run.
3. Advances one more day. Nov 1's daily run closes October first, and November opens with personal care at exactly the accepted $135 (`source="user"`), other flexible needs/wants lines within ±15% of October's, fixed lines (rent, savings) at October's actuals, and the free line equal to the total minus every other line (here $4,200 − $1,500 − $500 − $135 = $2,065).

**Why it's there:** It's the ticket's acceptance test: detection, diagnosis, the human decision and the adaptation at close, all wired together the way a user would live through them.
