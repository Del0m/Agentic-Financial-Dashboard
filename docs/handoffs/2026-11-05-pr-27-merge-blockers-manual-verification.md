# PR 27 merge blockers: manual verification

Covers the merge of `main` into TPC-27, the scrubbed advice text and the date handling in `backend/app/kg/retrieve.py`.

## 1. The merge

```bash
git log --oneline -1 --merges
```

Expect `merge main into TPC-27`. Then check both sides of the eval survived:

```bash
grep -n "no_numbers\|crossed_on\|allowed_tools\|temporal:" eval/fixtures.py
```

```bash
grep -n "tools_none\|no_numbers" eval/scenarios/abstain-credit-score.json
```

Each of the four `eval/scenarios/budget-graph-*.json` files should now have `crossed_on`.

## 2. The tests

```bash
cd backend && ../venv/bin/python -m pytest tests -q
```

Expect every test to pass. The new cases are at the end of `backend/tests/test_kg_retrieve.py`.

## 3. In the app

Log in as a seeded user with recurring charges, and ask in chat. The answer should carry the `graph` chip.

| Ask | Expect |
|---|---|
| "Has my gym membership gone up in price?" | The real first month and any price steps, not "first charged this month" |
| "How much did I spend at <merchant> in 2025?" | All of 2025 and a total, not only June |
| "How has <merchant> changed since 2025?" | Runs up to today |
| "What did I decide about <category> in <month>?" | Finds advice dated mid-month |
| "What's my food and drink budget?" | This month's limit only |

## 4. Privacy

Find a stored alert whose text names a P2P payment, or plant one, then ask about that category. Check the newest `graph` row in the prompt audit log (`PROMPT_AUDIT_PATH`).

Expect `Venmo to [person]` and no name. It also drops the rest of that sentence, because the scrubber treats everything after the service name as the counterparty.

## 5. Benchmark results

Any results recorded before these changes used the old retrieval, and `eval/compare.py` refuses to compare runs from different commits. Commit first, then re-run `eval/run_benchmark.py` before quoting numbers.
