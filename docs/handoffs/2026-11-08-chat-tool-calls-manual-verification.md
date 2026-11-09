# Chat tool-call box: manual verification

Each chat answer now opens with a **Tool calls** box. It lists every tool the agent called, in order. Each row shows the tool's name in words and in code, the arguments the model sent, whether it is running, done or failed, the clock time it ran, and how long it took. It replaces the small `get_debts ✓` chips.

## What changed

| File | Change |
|---|---|
| `backend/app/agents/chat_graph.py` | The tools node times each call. Every `ToolMessage` gets `additional_kwargs = {"started_at": <epoch ms>, "ms": <duration>}`. The Ollama client doesn't send `additional_kwargs`, so the model's input hasn't changed. |
| `backend/app/api/chat.py` | The `tool_call` event adds `at`, the time the model asked for the tool. The `tool_result` event adds `started_at` and `ms`. `GET /chat/{thread}` returns `started_at` and `ms` on each tool, so a reload still shows the timing. Tool results still stay on the server. |
| `frontend/lib/api.ts` | The optional timing fields on `ChatTool` and the chat events. |
| `frontend/components/ChatPanel.tsx` | `ToolChips` is replaced by `ToolCalls`, a collapsible `<details>` box above the answer. While a tool runs it reads "Using tools…" and the row pulses "running". After the last result, "The model is reading the results…" shows until the answer starts. |

Chats saved before this change have no timing. Their rows show the tool and status without a time.

## Check it

1. With the app and Ollama running, open http://localhost:3000/chat and click **New chat**.
2. Ask: `Which debt should I pay first, and what did I spend on food last month?`
   - While the model works, a box appears above the answer. It reads **Using tools…** and has an amber **running** row.
   - When it's done, the box reads **Used 2 tools**. The rows are **Debts** `get_debts` (no arguments) and **Spending** `get_spending` with `category: FOOD_AND_DRINK, start_date: …, end_date: …`. Each is green **done**, with a time like `6:52:29 PM · 28 ms`.
3. Reload the page. The same box comes back with the same times. Don't switch between the demo and linked views on the dashboard first: each view is a separate user, with its own chat history.
4. Click the box's header. It collapses to one line. Click again to open it.
5. Ask something that needs no tools, such as `What is the 50/30/20 rule?`. If the model answers from the guidance without calling a tool, that answer has no box.
6. Optional: a failed tool shows a red **failed** row. The model rarely sends a bad call on its own. The backend test `test_failed_tool_reports_not_ok` covers this case.

## Tests

- Backend: all 1,988 tests pass (`python -m pytest -q` from `backend/`). Two are new: `test_tool_events_say_when_they_ran` and `test_tool_result_records_its_timing`. The existing event and history tests now ignore or check the timing fields.
- Frontend: all 170 tests pass (`npm test`), including 3 new ones for the box: rows, status, arguments and timing; running, then the model reading the results; no box when no tool was called. ESLint and `tsc` are clean.
- In the browser, against the running Docker stack: the box showed live, a reload restored its times, there were no console errors, and the phone width had no horizontal scroll.
