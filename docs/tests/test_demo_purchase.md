# Tests: `backend/tests/test_demo_purchase.py`

These tests cover `backend/scripts/demo_purchase.py`, which adds a purchase mid-demo. Without `--plaid`, it writes the purchase straight into the seeded demo user's transactions. With `--plaid`, it sends the purchase to Plaid Sandbox for the user whose banks are linked through Plaid (`dev@localhost`). Plaid then assigns the category, and the app's own sync brings the purchase in on the next budget run. Plaid is faked here: `plaid_post` records each call and replies from a list.

Run them from `backend/`:

```bash
python -m pytest -q tests/test_demo_purchase.py
```

## Shared setup

- `plaid_user` adds the `dev@localhost` user.
- `link(db, user, n)` adds a linked bank with an encrypted access token `access-n`, or a given token or status.
- `plaid` replaces `plaid_post` and returns the calls it saw and a list of replies to give.

---

## `test_push_sends_the_purchase_to_the_bank_that_takes_it`

**What it does:** With two linked banks, the newest refuses (`INVALID_FIELD`) and the older one takes the purchase. Both calls go to `/sandbox/transactions/create` with the decrypted tokens, newest first. A three-month subscription goes in one call, with every date and an amount of 15.49 dollars.

**Why it's there:** Only a bank linked as `user_transactions_dynamic` accepts new transactions, and the script can't tell which one that is without asking. One call for all the dates matters because Plaid rate-limits this endpoint.

## `test_push_skips_seeded_and_inactive_banks`

**What it does:** A seeded placeholder bank and a bank whose sync failed don't count. The push says no bank is linked through Plaid, and Plaid isn't called.

**Why it's there:** Seeded users have a fake token, and sending it to Plaid would only fail.

## `test_push_explains_when_no_bank_takes_new_transactions`

**What it does:** One bank refuses and another has a token that can't be decrypted. The error names both (`INVALID_FIELD`, `InvalidToken`) and says to link with `--dynamic`. The undecryptable token is never sent to Plaid.

**Why it's there:** The presenter needs to know how to fix it on the spot.

## `test_push_stops_at_the_rate_limit`

**What it does:** When Plaid answers `SANDBOX_TRANSACTIONS_CREATE_LIMIT`, the push stops after that one call, with a message to wait a minute.

**Why it's there:** Trying the other banks would only use up more of the limit. Plaid rate-limited the real Sandbox after about four calls in a minute.

## `test_main_plaid_sends_a_preset_for_the_plaid_user_and_writes_nothing`

**What it does:** `laptop --plaid` sends "ElectroHub laptop" for $1,200.00, dated on the given day, to the `dev@localhost` user's bank. It prints what it sent, and writes no transaction itself.

**Why it's there:** The point of `--plaid` is that the purchase comes in through Plaid's sync, like a real card swipe. A row written by the script would skip that.

## `test_main_plaid_custom_purchase_needs_no_category`

**What it does:** `--plaid --merchant "Sushi Go" --amount 64.20` works without `--category`, and the description sent is "Sushi Go".

**Why it's there:** Plaid categorizes the purchase itself, so asking for a category would mean nothing.

## `test_main_plaid_without_a_bank_says_how_to_link`

**What it does:** With no linked bank, `laptop --plaid` exits 1 and prints `sandbox_link.py --dynamic`.

**Why it's there:** It's the first thing that goes wrong on a fresh setup.

## `test_main_plaid_cannot_undo`

**What it does:** `--plaid --undo` exits 1 and says Plaid Sandbox can't take a transaction back.

**Why it's there:** `--undo` only removes rows the script wrote itself. Pretending to undo a Plaid purchase would mislead the presenter.

## `test_main_without_plaid_still_writes_to_the_seeded_user`

**What it does:** `coffee` for the seeded demo user writes one "Blue Bottle Cafe" $6.50 row dated on the given day, and doesn't call Plaid.

**Why it's there:** The scripted demo still uses the direct path, so the Plaid change mustn't break it.
