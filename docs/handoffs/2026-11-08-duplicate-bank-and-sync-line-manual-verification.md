# Duplicate Plaid bank removed, demo sync line hidden: manual verification

## The duplicate bank (data only, no code)

The Plaid user (`dev@localhost`) had First Platypus Bank linked twice: item 13 at 6:47 PM and item 15 at 8:15 PM on 2026-11-08. Both had the same seven accounts, so every spending total counted twice. Item 13 is the `user_transactions_dynamic` bank: it holds the three Electric Hub demo purchases and is the one `demo_purchase.py --plaid` writes to. Item 15 was removed:

- Plaid `/item/remove` revoked its Sandbox access token.
- Its 362 transactions, 2 liabilities, 7 accounts (ids 73 to 79) and its `plaid_items` row were deleted in one database transaction.
- No budget run, alert, recurring update or graph edge had been built since item 15 was linked, so nothing derived needed fixing.

The deleted rows were saved to a JSON backup first, in Claude's scratchpad for that session (`plaid-item-15-backup.json`). That backup isn't in the repo.

### Check it

1. Open the dashboard in the **Plaid bank** view. It lists one First Platypus Bank, with seven accounts, not fourteen.
2. In chat, ask `What did I spend on food in the last 30 days?`. It's about $1,849, not $3,698.
3. Run a budget check on Alerts. The Agent activity line reads "Bank sync through Plaid: … from 1 linked bank."

Don't link the bank again unless you mean to. Each link adds another full copy of the accounts. The app has no unlink feature yet.

## "Bank sync: skipped" line

The Agent activity panel no longer shows "Bank sync: skipped. This user's data is seeded for the demo, not linked through Plaid." on demo-user runs. `syncLine` returns nothing for a skipped sync, just as it does for a run with no sync record. Plaid-user runs still say what the bank feed brought in.

| File | Change |
|---|---|
| `frontend/components/AgentActivity.tsx` | `syncLine` returns `null` when the sync was skipped. |
| `frontend/components/AgentActivity.test.tsx` | The demo-user test now expects no line. |
| `docs/tests/AgentActivity.test.md` | The test's description matches. |

### Check it

1. Open http://localhost:3000/alerts in the **Demo user** view. The Agent activity panel shows the pipeline and trace with no "Bank sync" line.
2. Switch to **Plaid bank** and click **Run budget check**. When it finishes, the "Bank sync through Plaid: …" line is still there.

### Tests

- `npm test -- components/AgentActivity.test.tsx app/alerts`: 30 pass. ESLint and `tsc` are clean.
- In the browser, the demo view's latest three runs all have `sync: {skipped: true}` and show no sync line. No console errors.
