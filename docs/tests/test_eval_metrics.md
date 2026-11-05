# Tests: `backend/tests/test_eval_metrics.py`

These tests cover `eval/metrics.py`, which scores one eval run (TPC-16) three ways:
- a **rule check** on the facts the scenario lists (numbers within a tolerance, or case-insensitive text), plus forbidden strings;
- **semantic similarity** between the answer and the golden answer;
- a **coherence judge** (`gemma3:12b`).

It also checks whether the expected tools were called and the expected sources retrieved, and it summarizes many runs. `mape` (TPC-18) scores the budget accuracy eval instead of chat runs. The facts check is deterministic on purpose, so a wrong number fails loudly however fluent the answer is.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_eval_metrics.py -v
```

## Shared setup

- `expect(...)`, `scenario(...)` and `record(...)` build an `Expect`, a `Scenario` and a stand-in for a runner `RunRecord`. `score_run` only reads the record's attributes, so a `SimpleNamespace` is enough.
- `FakeEmbeddings(vectors)` returns preset vectors and records the texts it was asked to embed.
- `Explodes` fails the test if anything on it is used. It stands in for a model that must not be called.
- `scored(...)` builds one `{"record", "scores"}` item in the shape `run_eval.py` writes, for `summarize`.

---

## `test_extract_numbers_formats`

**What it does:** Pulls `3200`, `24.99` and `-12.5` out of "$3,200.00 at 24.99% and 24.99 percent, refund -12.50". It also checks that "about 3.2k" yields neither 3200 nor 3.2.

**Why it's there:** Models write money and rates many ways. The fact check has to see through `$`, `%` and thousands commas, and it must not count an abbreviated number as matching, so a vague "3.2k" never passes for "$3,200".

## `test_401k_does_not_match_401_fact`

**What it does:** A number fact of 401 is *not* found in "put it in a 401(k) plan".

**Why it's there:** "401(k)" shows up in every retirement answer. If it counted as the number 401, a scenario with a 401 fact would pass by accident.

## `test_number_fact_tolerance`

**What it does:** A fact of 3200 ± 1 matches "$3,200.99" but not "$3,201.50".

**Why it's there:** It pins the tolerance edge, so a scenario author knows exactly what a `tol` allows.

## `test_text_fact_case_insensitive`

**What it does:** "platinum card" is found in "Pay the PLATINUM CARD first".

**Why it's there:** Capitalization isn't something the benchmark should punish.

## `test_forbidden_fails_complete_answer`

**What it does:** An answer with every fact but also a URL, when `http` is forbidden, has 2/2 facts found but `passed` false, with `forbidden_hits == ["http"]`.

**Why it's there:** The chat prompt forbids URLs because local models invent them (spec amendment A1 in TPC-15). The benchmark has to catch that even when the numbers are right.

## `test_missing_lists_labels`

**What it does:** When nothing matches, `missing` lists the facts' labels ("Platinum Card", "24.99±0.01") and the score is 0.

**Why it's there:** The per-run result file has to say *which* fact was missed, not just how many, so a failing scenario can be diagnosed from the results alone.

## `test_tools_and_sources_subset`

**What it does:** Checks that:
- an extra tool call doesn't fail `tools_ok`;
- a missing expected tool does;
- an empty expectation passes.

The same three rules apply to `sources_ok` with `source_url`s.

**Why it's there:** Tool choice and retrieval are reported next to the facts, so it's clear whether a wrong answer came from the wrong tool, the wrong guidance, or the model's own wording.

## `test_similarity_cosine`

**What it does:** With vectors [1,0] and [1,1], similarity is 1/√2. Both texts are sent with the `clustering: ` prefix.

**Why it's there:** It pins the cosine maths and the nomic task prefix. The search prefixes are asymmetric and would skew a symmetric comparison.

## `test_similarity_empty_answer_is_zero`

**What it does:** A blank answer scores 0.0 without calling the embedding model.

**Why it's there:** An empty answer has nothing to compare, and some embedding servers error on empty input. Scoring it 0 keeps one bad run from crashing the eval.

## `test_score_errored_run_skips_models`

**What it does:** Scoring a run whose `error` is set gives similarity 0.0, no judge result, `judge_passed` false and 0 facts found. Neither model is called.

**Why it's there:** An errored run should cost nothing extra and count as a failure everywhere, not be skipped. Skipping it would quietly inflate the pass rates.

## `test_summarize_counts_errors_and_rates`

**What it does:** Summarizes three runs (one errored, one at the similarity target, one below) and checks:
- the run and error counts;
- the fact, judge, similarity-at-target and tool rates;
- the judge score means over non-null judges;
- mean latency and p95 latency (nearest rank, so the max for 3 runs);
- the per-scenario min and max of fact score and similarity.

**Why it's there:** The summary is what goes in the writeup, so its arithmetic is pinned here rather than trusted.

---

## Coherence judge

`judge()` sends `gemma3:12b` the question, the tool results the agent got and the answer. The judge replies with JSON scoring `coherent`, `consistent_with_data` and `safe` from 1 to 5. A run passes if all three are at least `JUDGE_PASS` (4). These tests use `ScriptedChatModel` from `tests/fakes.py` in place of the judge. `judge_with(*replies)` runs one judgement and returns the result and the fake model.

## `test_judge_parses_json`

**What it does:** A valid reply gives scores 4/5/4, which pass. The prompt the judge saw contains the question, the tool result and the answer.

**Why it's there:** "Consistent with data" only means something if the judge sees the same numbers the agent saw.

## `test_judge_extracts_from_code_fence`

**What it does:** A reply that wraps the JSON in a code fence with chatty text around it still parses.

**Why it's there:** Local models often add a fence or a preamble even when told not to. Rejecting those would score many good answers as "no verdict".

## `test_judge_retries_once`

**What it does:** "not json" followed by valid JSON gives a result after exactly 2 calls.

**Why it's there:** One malformed reply is common and cheap to retry.

## `test_judge_two_failures_returns_none`

**What it does:** Two unparseable replies give `None` after exactly 2 calls.

**Why it's there:** It caps the cost. A judge that never produces JSON can't stall the eval, and the run counts as not passing.

## `test_judge_out_of_range_rejected`

**What it does:** `"coherent": 7`, twice, gives `None`.

**Why it's there:** A score outside 1-5 means the judge misread the rubric. Accepting it would distort the means.

## `test_judge_model_exception_returns_none`

**What it does:** A judge model that raises `TimeoutError` twice gives `None`, with no exception escaping.

**Why it's there:** A slow or crashed judge model must not abort a 15-run evaluation partway through.

## `test_judge_pass_threshold`

**What it does:** Scores of 3/5/5 parse but don't pass.

**Why it's there:** It pins the rule that every one of the three scores must be at least 4. A high average can't hide one weak dimension.

## `test_score_run_uses_judge`

**What it does:** Scoring a normal run calls the judge and stores its scores, with `judge_passed` true.

**Why it's there:** It connects the judge to `score_run`, the function the CLI calls.

## `test_judge_told_when_expected_tools_missing`

**What it does:** With `missing_tools=["get_debts"]`, the judge's prompt says the assistant did not look up the user's data with `get_debts`.

**Why it's there:** In the first real run, an answer that never looked at the user's debts was scored fully "consistent with data", because there were no numbers to contradict. The rubric now caps `consistent_with_data` at 2 when this note is present.

## `test_score_run_passes_missing_tools_to_judge`

**What it does:** When a scenario expects `get_debts` and the run never called it, `score_run` passes that to the judge.

**Why it's there:** It connects the scenario's expected tools to the judge's note in the path the CLI actually runs.

## `test_mape_known_case`

**What it does:** Limits of 110, 90 and 200 against actuals of 100 give errors of 10%, 10% and 100%, so the MAPE is 40.0.

**Why it's there:** It pins the definition used for "initial budget accuracy" (architecture 10.2). Over and under count the same, and each line weighs the same however big its category is.

## `test_mape_empty`

**What it does:** No pairs gives `None`, not 0 or an error.

**Why it's there:** A profile whose lines all had $0 actual has nothing to score. Reporting 0% would look like a perfect budget.

## `test_mape_rounds`

**What it does:** One pair (1, 3) gives 66.7.

**Why it's there:** Results are compared across runs and written to the decision log, so they're rounded to one decimal place.

## `test_errored_run_fails_tools_and_sources`

**What it does:** Scores an errored run of a scenario that expects no tools and no sources, and checks that `tools_ok` and `sources_ok` are both `False`.

**Why it's there:** An empty expected set is a subset of anything, so a crashed run used to pass both checks. If Ollama timed out on every run of `kb-roth-vs-401k` (which expects no tools), tool accuracy read 100% for a scenario that never answered, and overall source accuracy stayed above 80% even when every run crashed. A run with an error now fails both. The review on #10 found it.

## `test_number_tolerance_is_symmetric`

**What it does:** With the fact 124.25 ± 0.01, checks that `$124.24` and `$124.26` both pass and `$124.27` fails.

**Why it's there:** `abs(124.24 - 124.25)` is `0.0100000000000051` in floating point, so a value exactly one tolerance away passed on one side and failed on the other. The comparison now allows a tiny epsilon. The review on #10 found it.

---

## Abstention, pass^3, and budget and goal scoring (TPC-26)

## `test_abstain_passes_when_it_says_so`, `test_abstain_fails_without_saying_so`

**What they do:** Several ways of saying the data isn't there pass, including with a curly apostrophe. A confident answer without one fails.

**Why they're there:** Architecture 10.2: an abstain scenario passes only if the answer says the information isn't available.

## `test_abstain_fails_on_a_dollar_amount`, `test_abstain_allows_a_bare_year`

**What they do:** `$1,200`, `1200 dollars` and `300 USD` fail even with the right phrase. A bare `2019` doesn't.

**Why they're there:** Saying "I don't have it, but about $1,200" is still inventing a figure, yet repeating the year from the question isn't.

## `test_abstain_still_honours_forbid`

**What it does:** A forbidden string fails an abstain answer.

**Why it's there:** The rule against invented links applies to every chat answer.

## `test_budget_flags_are_exact`, `test_budget_flags_left_out_are_not_checked`

**What they do:** The right flag passes, an extra or missing one fails and is listed, and leaving `flags` out skips the check.

**Why they're there:** An extra flag is a false alarm. A signal scenario isn't about flags, so lumpy pace flags on its user don't fail it.

## `test_budget_cause_scores_the_first_alert`

**What it does:** Only the first alert for a category decides its cause, and no alert fails the check.

**Why it's there:** The first alert is what the user acts on. A later correct cause doesn't undo it.

## `test_budget_action_accepts_a_set_and_bans_any_alert`

**What it does:** Any accepted action passes on the first alert, an action outside the set fails, and a banned action fails on any alert in the window.

**Why it's there:** The agent's prompt allows more than one sensible action, but proposing a new budget for a one-off is wrong whenever it happens.

## `test_budget_flag_by_is_a_deadline`, `test_budget_alert_kinds_must_all_appear`, `test_budget_message_facts_read_every_alert`, `test_budget_lead_time_takes_the_earliest_warning`

**What they do:** Pin the flag deadline, that every listed alert kind must appear, that message facts may appear in any alert, and that lead time is the earliest warning's. With no warning, the lead time check fails.

**Why they're there:** Each is one named check in the results, so its exact meaning is pinned here.

## `test_abstain_accepts_saying_there_was_no_spending`

**What it does:** "You did not spend any money at Costco last year." passes.

**Why it's there:** The first live baseline gave this correct answer, and the phrase list rejected it.

## `test_budget_lead_time_ignores_warnings_where_no_dip_was_planted`

**What it does:** A low-balance alert in a scenario with no `min_days_ahead` reports no lead time.

**Why it's there:** A planted overspend user's static $100 balance raises warnings one day ahead. The first live baseline averaged those in, which made the metric read 1.8 days when the planted dip was warned about 6 days ahead.

## `test_budget_fallback_scores_each_check_apart`

**What it does:** With the code's plain alert, the flag passes while cause and message fail, the run fails, and the failure count is reported.

**Why it's there:** The TPC-26 decision: the user was warned, so the flag counts, but the model's failure must stay visible for the model comparison.

## `test_budget_control_false_alarm`

**What it does:** A control is a false alarm with any flag, but not for a low-balance alert.

**Why it's there:** The false-alarm rate is about overspending. A low-balance warning is a different signal.

## `test_budget_error_fails_every_check`

**What it does:** A crashed run fails every check and isn't counted as a false alarm.

**Why it's there:** A crash would otherwise pass a control, whose only check is that nothing was flagged.

## `test_time_to_flag_counts_from_the_planted_crossing`, `test_time_to_flag_reports_a_missing_alert`

**What they do:** A flag and alert both on Oct 6 for an Oct 2 crossing score 4 days, not 0. A crossing with no alert is listed in `missed_alerts`.

**Why they're there:** This is the time-to-flag metric in architecture 10.2. Measuring between two agent outputs would hide late detection.

## `test_goal_status_required_banned_and_first`, `test_goal_over_cap_fails_even_with_the_right_kinds`

**What they do:** Pin each goal check, using priority rather than list order for `first_kind`. A plan exactly at the cap passes, one cent over fails, and an `ok` with no plans fails.

**Why they're there:** Affordability is the point of these scenarios, and the graph already enforces it, so a failure here means something is badly wrong.

## `test_goal_nothing_affordable_means_no_model_call`

**What it does:** A non-`ok` status passes only with zero attempts.

**Why it's there:** The architecture stops before the model when nothing is affordable.

## `test_goal_attempts_are_reported_not_failed`, `test_goal_error_fails_every_check`

**What they do:** Two attempts with a valid final plan pass and report 2. A crashed run fails everything.

**Why they're there:** Retrying is how the graph is meant to work, per the TPC-26 decision.

## `test_summarize_reports_pass_on_all_runs_and_tags`, `test_summarize_with_no_chat_runs`

**What they do:** Five passes in six runs is 83%, but only half the scenarios pass every run. Tags group the rates, and a scenario with two tags counts under both. A run with no chat scenarios still summarizes.

**Why they're there:** This is pass^3 from architecture 10.2. `--kind budget` has no chat runs.

## `test_summarize_budget`, `test_summarize_budget_without_controls`, `test_summarize_goal`

**What they do:** Pin the summary fields: pass rates, pass^3, false-alarm rate, each check's rate (`None` when no scenario sets it), time-to-flag, model failures, tags and per-scenario rows.

**Why they're there:** TPC-27 and TPC-28 chart these.

## `test_tool_choice_counts_missing_and_unlisted_tools`, `test_tool_choice_irrelevance_and_unset_counts`, `test_tool_choice_arguments`

**What they do:** Tool choice is scored on names (a required tool missing or an unlisted one called fails), arguments and irrelevance (any call when none fits fails). A count the scenario doesn't set is `None`, not a pass.

**Why they're there:** `tools_ok` only checks required tools are there, so extra calls never counted. Architecture 6b.5 asks for all three.

## `test_temporal_check_uses_whole_words`

**What it does:** The right year must appear and a wrong one mustn't, as whole words, so "May" isn't found in "Maybe".

**Why it's there:** A substring match would score ordinary words as wrong dates.

## `test_the_judge_sees_graph_facts_as_data_given`

**What it does:** The judge prompt includes the graph facts the model received.

**Why it's there:** The judge scores against the data given, so without these it failed correct answers from the graph. Vector guidance still isn't shown to it, so the paper has to say judge scores aren't given the same inputs across conditions.

## `test_abstain_no_numbers_rejects_an_invented_score`, `test_abstain_without_no_numbers_allows_the_year_asked_about`

**What they do:** "I don't have a full credit report, but your current credit score is 780" fails when `no_numbers` is set. Without it, the year in a Roth question is still fine.

**Why they're there:** A refusal phrase about something else shouldn't let an invented score pass as abstention.
