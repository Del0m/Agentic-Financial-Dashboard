# test_eval_benchmark.py

Tests for the TPC-27 benchmark scripts: `eval/compare.py`, `eval/run_benchmark.py`, `eval/sample_answers.py` and `eval/budget_improvement.py`. None needs a model.

## `test_compare_counts_wins_losses_and_ties_per_group`, `test_verdict_rule_matches_the_preregistration`, `test_scenario_passes_on_two_of_three`

**What they do:** Check per-group counts, the 2 / -2 verdict rule, and that a scenario passes on at least 2 of 3 runs.

**Why they're there:** These are the rules `eval/preregistration.md` fixed before any result existed.

## `test_compare_refuses_folders_that_differ`

**What it does:** Legacy folders with a different commit or whole-run scenario hash can't be compared.

**Why it's there:** A difference could come from the code and not the technology being tested.

## Subset comparisons

`test_compare_full_baseline_with_matching_kind_subset` checks full baselines against chat, budget and goal subsets using
per-kind hashes, in both comparison directions. `test_subset_comparison_requires_matching_provenance` rejects changed
commits, changed relevant hashes, missing kind hashes and legacy metadata that cannot prove subset compatibility.
`test_compare_refuses_to_silently_drop_a_missing_scenario` rejects unequal scenario sets rather than scoring only their
intersection. `test_retry_rates_exclude_kinds_outside_the_comparison` ensures a budget retry comparison excludes goal calls,
and a goal retry comparison excludes budget calls.

These checks let the benchmark reuse its full baseline without weakening scenario provenance.

## `test_retry_fire_rate_marks_a_mechanism_that_rarely_ran`, `test_budget_metric_reads_cause_and_action`

**What they do:** A mechanism firing on under 5% of calls, or never, is marked uninformative. The budget metric reads the cause and action checks.

**Why they're there:** "Never fired" must not be read as "didn't help".

## `test_every_condition_changes_one_factor_from_the_baseline`, `test_conditions_needing_the_graph_wait_for_it`, `test_finished_condition_needs_the_same_commit`, `test_benchmark_refuses_uncommitted_changes`, `test_allow_dirty_is_only_for_the_pilot`

**What they do:** Check the run list, that graph conditions wait for the graph, that a restart only skips work from the same commit, and that a real run needs a clean tree.

**Why they're there:** All reported numbers must come from one commit.

## `test_sample_hides_the_condition_and_covers_each_folder`, `test_sample_score_reports_agreement_with_the_judge`

**What they do:** The sample covers each folder, repeats with its fixed seed, and shows the reader no condition or judge result. Scoring reports agreement.

**Why they're there:** The hand-read check is meant to be blind.

## `test_no_ai_baseline_makes_no_proposals_and_closes_three_periods`, `test_collect_then_replay_applies_decisions_and_counts_unseen`, `test_merge_drops_model_names_and_duplicates`, `test_bad_decision_value_is_refused`

**What they do:** The baseline closes 3 periods with no proposals. Replay applies saved decisions and counts proposals with none. Merging removes model names, so decisions can't depend on the model.

**Why they're there:** The paper credits the agent only with its difference from the baseline, and the decisions have to be reproducible.
