# TPC-25 goal approval: how to verify it by hand

**What was built:** you can now generate goals from the app, accept or dismiss each one on its own, and see the accepted ones on the dashboard. An accepted goal becomes a line in next month's budget (for example "Debt payoff $583.00/mo"). Until then the month under way doesn't change. You can stop an accepted goal, and a proposal you never answer expires when the month turns.

**The ticket's done-when:** accepting a goal shows it on the dashboard and in next month's budget (checks 2, 3 and 5).

This takes 15 to 20 minutes, mostly waiting for the model. You use the browser and two terminals.

## Setup

You need branch `TPC-25` checked out and Ollama running with `qwen2.5:14b` (`ollama list` should show it; otherwise `ollama pull qwen2.5:14b`).

Everything runs on a throwaway database in a temp folder, so your `dev.db` and checkpoint files aren't touched. Stop any API on port 8000 and any web server on port 3000 first (the README's "Run locally" ones, or `docker compose`), because they use your real data.

**Terminal 1** (the database and the API). From the repo root:

```bash
cd backend
export TPC25_CHECK_DIR="$(mktemp -d /tmp/tpc25-manual.XXXXXX)"
export DATABASE_URL="sqlite:///${TPC25_CHECK_DIR}/goals.db"
export PROMPT_AUDIT_PATH="${TPC25_CHECK_DIR}/audit.jsonl"
export BUDGET_CHECKPOINT_PATH="${TPC25_CHECK_DIR}/budget_checkpoints.db"
export CHAT_CHECKPOINT_PATH="${TPC25_CHECK_DIR}/chat_checkpoints.db"
# the same variables for the terminal you'll run commands in later
cat > "${TPC25_CHECK_DIR}/env.sh" <<EOF
export DATABASE_URL="${DATABASE_URL}" PROMPT_AUDIT_PATH="${PROMPT_AUDIT_PATH}"
export BUDGET_CHECKPOINT_PATH="${BUDGET_CHECKPOINT_PATH}" CHAT_CHECKPOINT_PATH="${CHAT_CHECKPOINT_PATH}"
EOF
echo "other terminals: cd backend && . ${TPC25_CHECK_DIR}/env.sh"

../venv/bin/alembic upgrade head
../venv/bin/python scripts/seed_budget_user.py --profile goals-card-24 --replace --start 2026-11-02
```

**Expected:** the last migration line ends `a3d9e1b7c2f5, goal start date`, and the seed prints `seeded budget-goals-card-24` and `simulated from 2026-11-02`.

The `--start` matters. It makes the user a simulated one whose "today" is 2026-11-02. Without it the dashboard follows your real clock, finds no budget for today's month, and says "No budget yet" after you advance the month.

Then start the API in the same terminal. Add `ENABLE_ADMIN=1` if you'd rather advance the date from the Alerts page than from the terminal (check 5):

```bash
DEV_USER_EMAIL=goals-card-24@eval.local ../venv/bin/uvicorn app.main:app --port 8000
```

**Terminal 2** (the website):

```bash
cd frontend
npm run dev
```

**Terminal 3** (commands): `cd backend && . <the folder printed by the echo above>/env.sh`. You use it for `curl` and for advancing the date.

Open http://localhost:3000/dashboard. `next dev` may re-add its block to `frontend/AGENTS.md`; that file is the only change it makes, and you can ignore it.

The user has $4,200 a month of income, a $3,200 card at 24.99% (minimum $95), an $18,400 student loan at 5.5% (minimum $210), and $1,000 saved. Its history ends on 2026-10-31. Its monthly surplus is **$971.75** after the card's minimum, and the cap on all goals together is 80% of that, **$777.40**.

## Checks

The model chooses the goals and the monthly amounts, so those can differ from run to run. In the recorded run (2026-11-02) it proposed $583.00 and $194.40 a month. One rerun, while writing this, gave $389.00 and $388.40. What doesn't change is the surplus, the cap, the two debts' figures for a given amount, and that the amounts together never pass $777.40. Where a check names an amount, use whatever your run proposed. The numbers below are the recorded run's.

### 1. Generate goals

On http://localhost:3000/goals click **Generate goals**. The button reads "Generating…" for 20 to 60 seconds.

**Expected:** the status line says "2 goals proposed." and two cards appear under **Proposed**:
- **Debt payoff (avalanche)**: $583.00/mo, "Target $21,600.00 by" February 2029, "Joins your budget in December 2026 if you accept."
- **Emergency fund**: $194.40/mo, "Target $6,851.78 by" November 2029, with the same "Joins your budget in December 2026" line. (It's sometimes the other order of amounts, or a different split; the two must add up to at most $777.40.)

Each card has **Accept** and **Dismiss**. The debt goal comes first.

### 2. Accept the debt goal

Click **Accept** on the debt payoff card.

**Expected:** the card moves under **Accepted**, with "In your budget from December 2026" and a **Stop** button. The emergency fund card stays under **Proposed**.

Open the dashboard (**Dashboard** link, top right).

**Expected:**
- The header link reads **Goals (1)**, for the proposal still waiting.
- The **Goals** card lists "Debt payoff (avalanche)", "From December 2026" and "$583.00/mo".
- The **Budget** card has no debt payoff line. This is November's budget, and an accepted goal never changes the month under way. (If it says "No budget yet", that's normal until the first simulated day runs in check 5; the next check works either way.)

### 3. Check what the API says

In terminal 3:

```bash
curl -s "localhost:8000/goals?status=accepted"
```

**Expected:** one goal, `"kind":"debt_payoff"`, `"monthly_cents":58300`, `"start_date":"2026-12-01"`, `"status":"accepted"`.

### 4. The 409 and 404 cases

```bash
curl -s -w ' %{http_code}\n' -X POST localhost:8000/goals/1/accept
curl -s -w ' %{http_code}\n' -X POST localhost:8000/goals/999/accept
```

**Expected:** the first says `{"detail":"This goal was already decided."} 409` (use your debt goal's id if it isn't 1). The second says `{"detail":"Goal not found."} 404`.

Stopping is covered in the edge cases below, after check 5, so the goal is still accepted while you advance.

### 5. Advance to the next month

Leave goal 2 (the emergency fund) undecided. In terminal 3, run:

```bash
../venv/bin/python scripts/simulate.py --user budget-goals-card-24 --days 29
```

Or, with the API started with `ENABLE_ADMIN=1`, open the Alerts page and use the simulation panel: **To month end** (that's 28 days, to 2026-11-30), then **Advance 1 day**.

**Expected:** with the script, 29 lines ending `2026-12-01  +0 txns  ok  flags: -  alerts: 0` and `simulated 29 day(s): 2026-11-02 → 2026-12-01, 29 runs, 0 alerts`. It takes a few seconds, because no day has any spending to flag, so the model isn't called. (You can't run it while a run is in progress: it says "A budget run is already in progress.")

Reload the dashboard.

**Expected:**
- The **Budget** card is December. It has a line **Debt payoff** showing **$583.00/mo** with no bar (a goal line has nothing spent and no pace).
- **Free** is **$589.21**. The budget total is **$4,200.00** and the lines add up to it. (With a $389.00 goal, Free is $783.21: Free plus the goal is always $1,172.21 here.) The goal came out of Free, and no other line changed because of it. Other lines move a little each close, as the budget smooths toward actual spending.
- The **Goals** card still lists the debt goal. The header says **Goals**, with no count: the undecided emergency fund proposal expired when November closed.

Back on /goals the emergency fund card is gone, and **Accepted** shows the debt goal.

```bash
curl -s -w ' %{http_code}\n' -X POST localhost:8000/goals/2/accept
```

**Expected:** `{"detail":"This proposal expired. Generate goals again."} 409`. It's too late to accept an expired proposal; generate again.

## Edge cases worth trying

These use the same setup. To start over, stop the API, delete the temp folder as under "How to undo", and repeat the setup.

**Dismiss a proposal.** Generate, then **Dismiss** one card.
- **Expected:** it disappears. `curl -s localhost:8000/goals/runs/<run id>` shows the run finish as `"status":"ok"` once the last proposal is decided.

**Stop an accepted goal and watch the line end at the next close.** With a goal accepted and the date at 2026-12-01 (after check 5), click **Stop** on its card, or run `curl -s -w ' %{http_code}\n' -X POST localhost:8000/goals/1/dismiss`.
- **Expected:** `200` and `"status":"dismissed"`. The December budget still shows the line (a month under way never changes). Stopping it again says `409`.
- Advance to the next month with `../venv/bin/python scripts/simulate.py --user budget-goals-card-24 --days 31`. **Expected:** January has no `Debt payoff` line. Ignore the rest of January (see the limits: this profile has no income after October, so January's total is about $2,905 and Free is $0).

**Generate again after accepting.** Accept a goal, then click **Generate goals** again.
- **Expected:** only kinds you haven't accepted are offered, and their amounts together are at most the cap minus what you accepted: $777.40 − $583.00 = **$194.40**. The earlier run's remaining proposal is replaced. `GET /goals/runs/<the first run>` says `"status":"expired"`.

**Other 409s and 404s.**
- Accepting a second goal of a kind that's already accepted gives 409 ("This proposal is older, or a goal of that kind is already accepted. Generate goals again."). That's hard to reach by hand; the tests cover it.
- Dismissing a goal that was already dismissed, completed or superseded gives 409.
- A goal id that doesn't exist, or belongs to another user, gives 404.
- `POST /goals/generate` while a run is going gives 409. The page's button is disabled while its own run goes.

**A proposal left over a month end.** Generate on 2026-11-02, answer neither card, advance to 2026-12-01 (check 5's command). **Expected:** both proposals are gone, the paused run's status is `expired`, and accepting either gives 409. This is the check 5 expiry, with both goals undecided.

**Nothing in the checkpoint file names your debts.** With a run paused (after check 1), in terminal 3:

```bash
grep -a -c "Platinum" "$BUDGET_CHECKPOINT_PATH" "$BUDGET_CHECKPOINT_PATH-wal"
```

**Expected:** `0` for both files. (`$PROMPT_AUDIT_PATH` does contain the card's name, as it always has: it logs the prompts sent to your local model.)

## Known limits that can look like bugs

- **No contribution tracking.** The app doesn't know what you actually pay toward a goal, so a goal line is a plan: no spent figure, no pace bar, never flagged. "$0 of $583" would be wrong, because card payments aren't spending and a transfer can't be tied to a goal.
- **The cap can be understated.** Once your payments toward a goal show up in your history, the measured surplus has already shrunk, and the accepted amount is then subtracted a second time. So a second proposal can be smaller than it needs to be until the goal ends. It errs on the safe side. TPC-31's forecast replaces this figure.
- **A debt goal's line ends on its projected date** (the month of its target date), even if the debt isn't paid off by then. Nothing tracks whether you paid.
- **This profile's history ends on 2026-10-31**, so advancing adds no transactions. Flexible lines shrink month by month as the budget smooths toward $0 of spending, and after December the income estimate falls too (January's total is about $2,905). None of that is the goal feature. Stop at 2026-12-01 for the clean comparison.
- **"Couldn't start Link"** appears on the dashboard for a simulated user. It's existing behaviour: a synthetic user has no bank to link.
- **A goal accepted or stopped during a budget close** can take effect one month late (rare, and it corrects itself the month after).
- **The failure line on /goals can show a raw code** such as `incomplete_card_obligations` instead of a sentence, and a run that finds nothing to propose can't say it withdrew the earlier proposal.
- **Accept and Stop stay clickable while Generate runs.** The server answers 409 if it matters.
- **If a run fails after saving its goals**, the page can say "failed" while the new proposal is already on the page. Reload and it's there.
- **Opening the goals list can finish a stuck run**, so a `GET` may write. That's how a click whose resume failed heals.
- **50/30/20 users:** a first budget with goal lines can also write a "group over target" alert that a first budget without goals doesn't.

## What was and wasn't verified

**Run while writing this handoff, on 2026-11-02, with Ollama and `qwen2.5:14b` running:**
- Setup: `alembic upgrade head` (head `a3d9e1b7c2f5`), the seed with `--replace --start 2026-11-02`, and the API on a spare port with `DEV_USER_EMAIL=goals-card-24@eval.local`, on a throwaway database.
- Through the API with `curl`: generate (proposed a $389.00 debt goal and a $388.40 emergency fund, awaiting approval), accept, the accepted list, accept again (409), generate again with the cap at $388.40 (the first run became `expired`), the 29-day `scripts/simulate.py` run to 2026-12-01 (29 runs, no alerts), December's budget (`Debt payoff` $389.00, group `goal`, nothing spent, no pace, Free $783.21, lines summing to the $4,200.00 total), the undecided proposal `superseded` with its run `expired` and a late accept 409 ("This proposal expired"), stop 200, stop again 409, unknown goal 404, and 31 more days, after which January has no debt line.
- The checkpoint file has no occurrence of the card's name.

**Recorded earlier the same day, live, through the browser and the real app** (`qwen2.5:14b`, simulated `goals-card-24`, a disposable database): Generate proposed `debt_payoff` (avalanche) at $583.00/mo, target $21,600.00, February 2029, and `emergency_fund` at $194.40/mo, target $6,851.78, November 2029, with a surplus of $971.75 and a cap of $777.40. Accepting the debt goal showed it on the dashboard as "From December 2026", with Goals (1) for the remaining proposal, and left the budget unchanged. Advancing 29 days to 2026-12-01 closed November: December has "Debt payoff $583.00/mo" with no meter, Free $589.21, a total of $4,200.00 and lines summing to $4,200.00, and the undecided emergency fund proposal expired. Accepting again gave 409, stopping gave 200, stopping again 409, and an unknown goal 404. The 375 px layout looked fine.

**Not run:**
- The browser steps of this handoff (the pages in checks 1, 2 and 5), and the Alerts page's simulation panel with `ENABLE_ADMIN=1`. The page behaviour is covered by the frontend tests and by the earlier live browser check above.
- The edge cases for dismiss through the page, generating while a run is going, and a stop through the page's **Stop** button (the API route behind it was run).
- Docker, a real linked bank account, and Postgres. Goals are only checked on SQLite and on synthetic data.

**Automated checks, 2026-11-02:**
- Backend: `../venv/bin/python -m pytest -q -k 'not live and not sandbox'` from `backend/`: **997 passed, 22 deselected**. That includes `tests/test_goal_loop.py` (the done-when end to end, 4 tests).
- Frontend, from `frontend/`: `npm test` **105 passed** (11 files), `npm run lint` clean, `npm run build` succeeded (routes `/`, `/alerts`, `/chat`, `/dashboard`, `/goals`).

**Diagrams:** `docs/diagrams/approval-lifecycle`, `data-model` and `remaining-lifecycle` were updated, and their `.png` and `.svg` files were regenerated with `plantuml` (installed here).

## How to undo

Stop the API (Ctrl+C in terminal 1) and the web server (Ctrl+C in terminal 2), then delete the temp folder:

```bash
rm -rf "$TPC25_CHECK_DIR"
unset DATABASE_URL PROMPT_AUDIT_PATH BUDGET_CHECKPOINT_PATH CHAT_CHECKPOINT_PATH TPC25_CHECK_DIR
```

Nothing here touched `dev.db` or your real checkpoint files. Switch back to your own branch with `git switch main` if you want your usual servers back.
