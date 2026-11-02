# TPC-24: Goal validation and bounded correction

Date: 2026-11-02. Status: approved and implemented; verification is recorded in the implementation plan and handoff.

Ticket: [TPC-24](https://ucf-ctcw-senior-design.atlassian.net/browse/TPC-24), “M4: Validate goals and retry when unaffordable.” Read through the Atlassian connector on 2026-11-02.

## Overview

Extend TPC-23's Python goal graph and CLI so a new proposal reaches storage only when every goal is affordable, dated, and consistent with structured preferences. Code rejects an invalid proposal with reasons and permits one correction call. The user receives either a completely valid new proposal or a clear failure/no-goals result.

The ticket's acceptance criterion is a deliberately tight-surplus test that never returns an unaffordable new goal. Its stated retry maximum is three; the user's stricter choice is two model calls total, including the initial call.

## Constraints and scope

- Keep Python, pytest, LangGraph, Pydantic, SQLAlchemy, FastAPI, and the existing Ollama integration.
- Reuse TPC-23's measured surplus, card-minimum deductions, preference cap, goal builders, and per-user lock.
- Money checks use integer cents and the existing decimal half-up rounding. Round each proposed monthly amount once before summing; validate those same cents that will be stored.
- Code computes targets and dates. The model continues to choose kinds, order, monthly amounts, strategy, and rationale.
- No new generation API, Goals UI, approval, or budget-line integration: those belong to TPC-25. The forecast replacement belongs to TPC-31.
- Write failing tests before each implementation slice and record actual red/green results. Planning and the pre-change baseline are not TDD evidence.
- The final handoff follows TPC-23's concise host/venv workflow, with a throwaway database and cleanup.

## Confirmed decisions

| Area | Decision | Why | Alternatives considered | Reversibility |
|---|---|---|---|---|
| Scope | Existing graph and CLI, tests, and handoff | Keeps the ticket focused | Generation API and UI | Easy; separate ticket |
| Proposal rejection | Reject the whole proposal if any goal fails | Allocation and priorities need reconsidering together | Keep or retry individual goals | Moderate; changes observable behavior |
| Model calls | At most two total calls across all failures | Explicit user choice; prevents nested retries | Three calls; separate parse/correction budgets | Easy; policy constant |
| Dates | Every new goal must finish within the existing 600-month horizon | A goal must be achievable and dated | Undated warnings; stricter duration | Moderate |
| Duplicate/unoffered kinds | Reject the whole proposal and permit correction | Consistent validation contract | Existing drop-and-continue behavior | Moderate |
| Failed attempts | Preserve the previous proposal and report no replacement | Failure gives no valid replacement to publish | Withdraw; deterministic fallback | Moderate |
| Preferences | Enforce structured fields in code; note remains model guidance | Structured rules are verifiable | Interpret arbitrary notes as hard constraints | Moderate |
| Cap below $1 | No model call; clear no-affordable-goals outcome; withdraw old proposal | Existing schema cannot produce a fitting goal | Error and preserve old proposal; sub-dollar goals | Moderate |
| TDD evidence | Record red/green commands and results for each meaningful slice | Makes test-first development reviewable | Final results only; separate failing commits | Easy |
| Handoff audience | User; concise host/venv instructions matching TPC-23 | Explicit user preference | Teammate/Docker guide; instructor report | Easy |
| Correction feedback | Same scrubbed facts, rejected normalized allocation, and all actionable validation reasons; no raw reply or rationale | Gives the model enough information to correct without echoing arbitrary text | First failure only; full conversation history | Easy |
| Live checks | Five tight-surplus trials when Ollama is available; report skips explicitly | Complements the deterministic safety proof with real-model behavior | Mandatory live success; deterministic tests only | Easy |
| Failure demonstration | Focused scripted pytest commands, plus a live CLI smoke check | Invalid-then-valid behavior is reproducible | Standalone script; live CLI only | Easy |
| Review boundary | Stop after presenting this design and the TDD plan | User explicitly requested review before implementation | Continue directly into implementation | Easy; user can authorize the next phase |

## Proposed structure

```text
backend/app/budget_engine/goals.py       # pure allocation and computed-plan validation
backend/app/agents/goal_llm.py            # proposal contract and correction messages
backend/app/agents/goal_graph.py          # shared call budget, validation routing, persistence/reporting
backend/app/privacy/gateway.py           # scrubbed correction context and audit
backend/tests/test_goals.py              # pure rule and money/date boundary cases
backend/tests/test_goal_graph.py         # retry, storage, lifecycle, CLI and live checks
backend/tests/test_gateway.py            # correction context privacy/audit coverage
backend/tests/test_goals_api.py          # observable storage outcomes and user isolation
docs/tests/                             # explanation of each added/changed test
docs/superpowers/plans/                  # implementation sequence and actual red/green evidence
docs/handoffs/                          # final manual verification instructions
```

Avoid importing agent/Pydantic types into the pure engine. Adapt the model reply to normalized allocations in integer cents at the boundary. Exact helper names are implementation defaults, not user-facing contracts.

## Proposed graph and contracts

```text
load_facts -> propose -> validate_candidate -> persist
                 ^               |
                 +-- correction -+   (only when one call remains)

validate_candidate: normalize -> allocation checks -> build safe candidates -> date/plan checks
```

`load_facts` retains the existing no-history, nonpositive-surplus, and nothing-offered outcomes. A cap below 100 cents with positive surplus and at least one offered kind adds `no_affordable_goals`, withdraws prior proposals, and makes no model call. Capture the debts and goal facts once so correction uses the same snapshot for amounts, plans, dates, and scrubbed context.

`propose` consumes exactly one call. Use `invoke_structured(..., retries=0, raise_unreachable=True)` so parsing cannot create hidden extra calls. Malformed/schema-invalid output consumes that attempt too. An unreachable model still fails immediately, preserving existing outage semantics.

Inside `validate_candidate`, pure allocation checks cover all kinds against the offered set, uniqueness, normalized monthly bounds, and the sum of monthly cents against the cap. Exact-cap allocations pass; one cent over fails. Missing debt strategy keeps the existing avalanche default. Any invalid allocation rejects the complete candidate.

The same validation node uses the existing pure goal builders without writing rows, then checks computed dates, positive targets, and consistency with normalized allocations. A null date rejects the complete candidate. Gather every independently computable failure in one pass, including dates for safe-to-build allocations even when their combined amount exceeds the cap. Skip construction for an allocation that cannot safely be built, reporting its validation failure instead. The existing low-level builders may continue returning null to describe impossible plans; the graph must refuse to publish them.

On correction, discard all earlier candidate plans and rebuild the entire reply using the same facts snapshot. Neither rejected attempt writes or supersedes goals. A successful proposal and superseding its predecessor commit together, as today.

### Correction feedback and audit

The second call receives the same scrubbed facts and a correction payload containing the rejected allocation's kinds, normalized monthly amounts, and strategies, plus every actionable validation reason computed by code. Identify goals by index/kind, never database user/account identifiers. Monetary reasons include code-computed total, cap, and excess; date failures explain the 600-month limit. Model-facing money remains in dollars; internal checks and run summaries remain in cents.

Malformed/schema-invalid output consumes an attempt and gets a fixed explanation of the expected contract. There is no safely validated rejected allocation to echo in that case. Do not pass through the raw reply, arbitrary rationale, or raw parser/exception messages.

Reuse the gateway's pseudonym and scrubbed snapshot. Audit the outgoing correction context, including its feedback and attempt number, through the gateway; the audit is a context record, not a claim to capture the full system/human message transcript. Return and record attempt counts and per-attempt validation issues in `AgentRun.summary_json` without raw model output. A successful correction retains the first attempt's failure evidence but clears it as an active blocker.

On exhaustion, return `status=error` with structured reasons, attempts, and no new goal IDs. Preserve prior proposed rows. The CLI explicitly says that no replacement was generated and earlier goals remain. Retained historical proposals are not certified against the new facts: the safety guarantee applies to newly generated/persisted goals. `GET /goals` retains its existing read contract.

## Defaults not separately discussed

- Keep the existing $1–$1,000,000 monthly schema bounds, one to three goals, priority order, 80% default cap, surplus measurement, and card-minimum policy.
- Compare dates to the computed plan start and the existing horizon; do not ask the model to supply dates.
- Do not enforce high-interest-first as a new deterministic rule; retain existing prompt guidance and its live check.
- No database migration: retain nullable dates for historical rows and use `AgentRun.summary_json` for new validation evidence.
- Keep existing per-user locking, interruption handling, privacy scrubbing, API authorization, and simulated-date behavior.
- No automatic rescaling, trimming, or deterministic fallback allocation.
- Failure reasons and call counts are available in the returned state, run summary, and CLI. Do not store raw invalid model output in run summaries.

## Verification and handoff

Use scripted responses for the ticket's safety proof: deliberately over-cap then valid, and over-cap on both attempts. Check new goal IDs, returned plans, stored rows, total cents, dates, call count, and preservation of earlier proposals. A failed run returns no rejected candidate plans as usable results; its feedback is diagnostic only.

Run five parameterized tight-surplus trials with the real configured Ollama model if it is available. A valid fixture is `goals-card-24` with `max_share=0.05`, giving a $48.59 cap from the existing $971.75 measured surplus; independently assert these values. Record each trial's outcome, call count, total, cap, and dates. An error/exhausted trial cannot publish an unsafe goal and is reported as failed usefulness, not silently counted as a successful live trial. If Ollama/model is unavailable, explicitly report five skipped trials.

The handoff uses focused scripted pytest commands to demonstrate rejection, correction, and exhaustion reliably, plus a live CLI smoke check on disposable data. Keep it concise and follow the host/venv instructions from TPC-23. Include setup, expected outcomes, cleanup, actual verification results, and limitations. Do not add a production debug flag or standalone verification script.

## Review and implementation status

The user reviewed and approved this plan before implementation. No product-policy questions remain. Tests were organized into dedicated `test_goal_validation.py` and `test_goal_validation_graph.py` modules; existing historical/API and graph coverage remains in its original modules.

## Risks and compatibility

- TPC-23 tests deliberately store over-cap and undated goals and drop repeated/unoffered kinds. Update those graph/CLI expectations to the selected stricter behavior. Do not remove low-level builder or historical API coverage merely because generation becomes stricter.
- Preservation after a failed run can leave an older proposal visible that no longer fits current facts. The CLI and handoff must identify it as retained rather than a successful new result.
- A successfully parsed second attempt can still fail allocation or date validation. It must end without a third call or partial persistence.
- The context audit currently records gateway facts, not full model message history. Correction evidence must preserve privacy and accurately describe what it records.
- A model can produce numerically inconsistent rationale text. This ticket validates computed fields; free-text numerical claims remain a separate limitation.

## Baseline and next steps

On the pre-change TPC-23 checkout, from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_goals.py tests/test_goal_graph.py tests/test_goals_api.py tests/test_goal_prefs.py -q -k 'not live and not sandbox'
# Observed: 78 passed, 1 deselected.
../venv/bin/python -m pytest -q -k 'not live and not sandbox'
# Observed: 773 passed, 16 deselected in 11.11s.
```

Implemented on `codex/tpc-24-goal-validation` after user approval. See the implementation plan for observed red/green evidence and the handoff for verified commands, outcomes, and limits.
