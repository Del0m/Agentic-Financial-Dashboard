# Agent activity panel: manual verification

The Alerts page now shows the budget agent working. While a run is going, it lights up each graph step as it starts. Afterward, it walks through each flagged line step by step and says who did each step: code, the model, memory or you.

## What changed

| File | Change |
|---|---|
| `backend/app/agents/budget_graph.py` | Each node reports its start. Diagnose and decide record a per-flag trace: measured numbers, earlier alerts and past advice, cause and evidence, the model's own choice, and which override (if any) changed it. Signals record the model's choice. The run summary gets a `trace` field. |
| `backend/app/budget_service/progress.py` | New. In-memory list of the steps each recent run has started. |
| `backend/app/api/budget.py` | New `GET /budget/activity?limit=N`: the user's newest budget runs with their steps and summary. |
| `backend/scripts/demo_purchase.py` | `--run` prints the trace. |
| `frontend/components/AgentActivity.tsx` | New panel: pipeline strip, five-step trace per flagged line, signals, and an earlier-runs picker. Polls every 0.8 s while a run or advance is going. |
| `frontend/app/alerts/page.tsx` | Shows the panel above the alert list. |
| `frontend/lib/api.ts` | `getActivity` and the trace types. |
| `frontend/components/AlertCard.tsx` | The free line's alerts are titled "Everything else", not "Free". |

The trace follows the run summary's rule: numbers, not merchant names or balances. The exception is the model's evidence text, which is the same kind of text the advice log and alerts already store.

## Check it

1. With the app and Ollama running, open http://localhost:3000/alerts. The **Agent activity** panel sits above Pending. Runs from before this change say they finished before decision traces were recorded.
2. Add a purchase and click **Run budget check**:

   ```bash
   docker compose exec backend python scripts/demo_purchase.py coffee
   ```

   While it runs, the steps light up in order and the current one pulses. The status line names it, for example "The model is on diagnose cause…".
3. When it finishes, each flagged line shows Measured, Remembered, Diagnosed, Decided and Checked, then a result. If the model stayed quiet because it had already alerted, step 2 says "Already alerted you once" and the result says "stayed quiet".
4. Click a date under **Earlier runs**. The panel switches to that run.
5. Click **Advance 1 day**. The panel follows the new day's run live.
6. Stop Ollama and click **Run budget check**. The trace says "The model gave no diagnosis" and "code wrote a plain alert instead". Start Ollama again afterward.
7. Optional, in the terminal: `docker compose exec backend python scripts/demo_purchase.py haircut --run` prints the same trace with `[code]`, `[memory]` and `[model]` tags.

## Tests

- Backend: 1,948 offline tests pass (`python -m pytest -q -k "not live and not sandbox"` from `backend/`), including 7 new ones for the trace, step order and the activity route.
- Frontend: 155 tests pass (`npm test`), including 9 for the panel and 1 for the AlertCard label. ESLint and `tsc` are clean.
- In the browser, on a separate copy of the database: the live steps lit up during a run, the finished trace matched the run, the phone layout had no horizontal scroll, and there were no console errors.

## Limits

- Live steps are kept in the API process's memory. A run started from the CLI (`--run`, `simulate.py`) shows its finished trace, but not live steps. A backend restart forgets live steps for earlier runs, and the panel then infers them from the trace.
- Not added: the chat agent's and goal agent's traces. Chat already shows tool chips and sources. Goals show validation and retry status only in the generate progress text.
