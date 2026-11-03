# Tests: `frontend/app/goals/page.test.tsx`

These tests cover the Goals page, `app/goals/page.tsx` (TPC-25):
- **The Generate button.** "Generate goals" starts a goal run, then asks `GET /goals/runs/{id}` every 2 seconds until it settles. It says what happened: how many goals were proposed, why there are none, or that the run failed and the earlier proposal stands.
- **The goal lists.** The proposal is under "Proposed" with Accept and Dismiss, and accepted goals are under "Accepted" with Stop.
- **Reloading.** The list reloads after every run and decision.

Run them from `frontend/`:

```bash
npm test -- app/goals/page.test.tsx
```

## Shared setup

- `@/lib/api` is mocked with `vi.mock`, as in `alerts-page.test.md`. `getGoals`, `generateGoals`, `getGoalRun`, `acceptGoal` and `dismissGoal` are scripted per test, and `errorMessage` and `ApiError` stay real.
- **Fake timers** (`shouldAdvanceTime`) let the 2-second poll run instantly.
- **`goal(id, over)`** builds a proposed goal, and **`run(over)`** builds a settled run.
- **`generateThrough(...runs)`** clicks Generate and advances the clock once per scripted status.

---

## `Generate polls until the run settles, then lists the proposal`

**What it does:** The first poll says `running` and the second `awaiting_user` with two goals. The page says "2 goals proposed." and the new goal appears. The goals are loaded with `"current"`.

**Why it's there:** A run takes tens of seconds and the proxy drops a request after 30 s, so the page polls. The reload shows the proposal without a refresh.

## `a run that finished with goals says how many`

**What it does:** An `ok` run with one goal id reads "1 goal proposed.".

**Why it's there:** A run that was never paused (no checkpointer) ends `ok`, and the count still has to read right, in the singular.

## `a run that hasn't reported back asks for a refresh`

**What it does:** When `POST /goals/generate` returns a null `run_id`, the page asks for a refresh, polls nothing, and re-enables the button.

**Why it's there:** The server waits 5 seconds for the run to start and then gives up the id. The alerts page handles it the same way.

## `outcome <outcome> says why there's no proposal`

**What it does:** For each of `no_surplus`, `nothing_offered` and `no_affordable_goals`, an `ok` run with no goals shows its own line: no money left over in a typical month, preferences and accepted goals already cover every kind, or what's left over is under $1.

**Why it's there:** An empty result looks like a failure. Each outcome has a different cause and a different thing the user can do about it.

## `a failed run says the earlier proposal stands and lists the reasons`

**What it does:** An `error` run with reason `validation_failed` and two issues shows "The model's proposal didn't fit after two tries. Your earlier proposal stands." (not the code) and both issue messages. The existing proposal is still listed.

**Why it's there:** A failed run writes nothing, so the user's proposal is untouched. The messages are code-written (never the model's text) and say what was wrong.

## `an interrupted run says it couldn't finish and names the error`

**What it does:** An `error` run with no `reason` but `error: "ConnectError"` shows "The run couldn't finish. Check that the model is running, then try again. Your earlier proposal stands. (ConnectError)".

**Why it's there:** A run closed on restart has only an error in its summary, and an unreachable model (Ollama down) is the usual cause. The user needs the next step, not just the exception name.

## `an exception name as the reason gets the same plain message`

**What it does:** An `error` run whose `reason` is `ConnectError` shows the same "The run couldn't finish" message with "(ConnectError)".

**Why it's there:** The graph can store an exception name as the reason, and it isn't one of the known codes.

## `incomplete card obligations explain why goals can't be worked out`

**What it does:** An `error` run with reason `incomplete_card_obligations` says "A credit card is missing its balance or minimum payment, so goals can't be worked out yet. Your earlier proposal stands.", without the code.

**Why it's there:** The backend refuses to plan when a credit account has an unknown balance or a balance with no known minimum. Without a sentence, Generate looked broken for no reason.

## `bad model output says no usable proposal came back`

**What it does:** An `error` run with reason `bad_model_output` says "The model didn't return a usable proposal after two tries. Your earlier proposal stands.", without the code.

**Why it's there:** The model's text twice failed to parse as a proposal, which is not the same as a proposal that didn't fit.

## `goals_changed asks the user to generate again`

**What it does:** A `goals_changed` failure says the accepted goals changed and asks the user to generate again, without showing the raw code.

**Why it's there:** The user accepted or stopped a goal while the run was working, so its cap was stale and it wrote nothing. Generating again is the fix.

## `an expired run says a newer proposal replaced it`

**What it does:** An `expired` run reads "A newer proposal replaced this one.".

**Why it's there:** A paused run whose proposals were superseded ends `expired`, which isn't a failure.

## `a run that never settles stops polling after 10 minutes`

**What it does:** A run that stays `running` is polled 300 times, then the page says "Still running. Check back later.".

**Why it's there:** A run whose server stopped never finishes. The page mustn't spin forever.

## `leaving the page stops polling`

**What it does:** After unmounting, at most one more poll is made.

**Why it's there:** An unfinished loop would keep calling the API from a page nobody is looking at.

## `a 409 on Generate shows the server's message and re-enables the button`

**What it does:** A `409` shows "A budget or goal run is already in progress." and Generate is enabled again.

**Why it's there:** One run per user at a time. The user should see why, and be able to try again.

## `Accept moves the goal to Accepted after a reload`

**What it does:** Accepting the goal calls `acceptGoal(1)`. After the reload, the "Accepted" section shows "In your budget from December 2026" and "Proposed" is gone.

**Why it's there:** The server is the source of truth; the page shows what it reloads, not a guess.

## `Stop on an accepted goal dismisses it`

**What it does:** Stop calls `dismissGoal(1)`, and the empty state shows after the reload.

**Why it's there:** Stopping an accepted goal uses the dismiss route, so a wrong click isn't a commitment for years.

## `a 409 on Accept shows the message and reloads, dropping the stale goal`

**What it does:** A `409` shows the server's message, the list reloads without the goal, and the message is still there afterwards.

**Why it's there:** A proposal can be replaced or its kind already accepted. The reload removes the dead card, and the message has to survive that reload or the user wouldn't know why it vanished.

## `no goals shows the empty state`

**What it does:** With no goals, the page says "No goals right now. Generate goals to get a proposal.".

**Why it's there:** An empty page with only a button reads as broken. "Yet" was wrong after a goal ends or is stopped.

## `a failed load says so`

**What it does:** When `GET /goals` fails, the server's message is shown.

**Why it's there:** The page must not sit on "Loading…" forever.
