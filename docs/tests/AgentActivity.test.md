# Tests: `frontend/components/AgentActivity.test.tsx`

These tests cover `components/AgentActivity.tsx`, the Agent activity panel on the Alerts page. While a run is going, it lights up the budget graph's steps as they start. When a run ends, it walks through each flagged line in five steps: what code measured, what memory held, the model's diagnosis, the model's choice, and code's check on that choice. It then lists the signals and what the model chose for each one. Each step is labelled with who did it: code, model, memory or you.

Run them from `frontend/`:

```bash
npm test -- components/AgentActivity.test.tsx
```

## Shared setup

- `flag(over)` builds a traced "Everything else" pace flag: $1,200 of $2,158 on day 12, diagnosed one-time, which became alert #8.
- `run(over)` builds a finished run whose trace holds that flag.

---

## `walks from measurement through the model's diagnosis to the alert written`

**What it does:** The flag shows "Everything else", the measured line ("$1,200.00 of $2,158.00 by day 12 of 31: 1.44× the pace"), no earlier alerts, the cause and evidence, "Within the rules", and "wrote alert #8".

**Why it's there:** This is the main view of the agent at work. Each step has to read correctly from the trace.

## `shows memory leading the model to stay quiet`

**What it does:** With one earlier alert and a `no_op`, the flag says it already alerted once, that the model chose to stay quiet, and that no new alert was written.

**Why it's there:** A quiet day is a decision made from memory. Without this view, it looks like nothing happened.

## `shows when code overruled the model`

**What it does:** A downgraded proposal shows the model's choice (propose next month's limit) and the rule that turned it into an alert.

**Why it's there:** It shows the model's choice and code's limits on it as separate steps.

## `says so when the model didn't answer`

**What it does:** With no cause and no model choice, the flag says the model gave no diagnosis and that code wrote a plain alert.

**Why it's there:** Fallback alerts can't be mistaken for the model's own choice. That mix-up made repeating alerts look like nagging while Ollama was down.

## `a proposal says it paused for the user's decision`

**What it does:** An `adjust_next_budget` flag shows the proposed $135.00 and says the run paused for the user's decision.

**Why it's there:** The human approval step is part of the loop and has to be visible.

## `a running run highlights the step it is on`

**What it does:** For a running run whose last started step is `diagnose`, "Diagnose cause" is the current step, earlier steps are done, later ones are not reached, and the status line names the step.

**Why it's there:** This is the live view during **Run budget check** and **Advance**.

## `a quiet day says the model wasn't asked, and skips the model steps`

**What it does:** A run with no flags says how many lines were checked and that the model wasn't asked. The model steps show as not reached.

**Why it's there:** Code only calls the model when a line is flagged.

## `lists signals with the model's choice`

**What it does:** A new-subscription signal shows "$15.49 monthly", the model's choice and its alert number.

**Why it's there:** Recurring charges and the cash forecast are the other places the model makes decisions.

## `an older run without a trace says so`

**What it does:** A run with an empty summary says it finished before decision traces were recorded.

**Why it's there:** Runs from before this change have no trace. They need to say so, not show a misleading empty view.
