# Tests: `backend/tests/test_eval_cli.py`

These tests cover `eval/run_eval.py`, the eval harness's command line (TPC-16). It:
- checks that Ollama, the three models and the knowledge base are ready;
- loads the scenarios;
- runs each one `--runs` times and writes each run's record and scores as soon as it finishes;
- ends with `summary.json` and `config.json` in `eval/results/<date>/<model>/`.

No test here calls a real model.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_eval_cli.py -v
```

## Shared setup

- `offline` points `SCENARIOS_DIR` at a temporary folder with two scenarios (`debt-highest-apr` and `second`, written with `write` from `test_eval_fixtures.py`), and `RESULTS_DIR` at a temporary folder. It also replaces everything outside the harness:
  - `KnowledgeBase` → `FakeKB`;
  - `get_chat_model` and `get_embeddings` → placeholder objects;
  - `preflight` → no problems;
  - `score_run` → one canned score.
- `fake_run` returns a canned `RunRecord` with answer "ok" and a 1-second latency.

---

## `test_results_dir_sanitizes_and_never_overwrites`

**What it does:** Calls `results_dir` three times for `qwen2.5:14b` on the same day. It gets `2026-10-27/qwen2.5_14b`, then `qwen2.5_14b-2`, then `qwen2.5_14b-3`, and each folder is created. A model named `a/b` becomes `a_b`.

**Why it's there:** Model names contain `:` and sometimes `/`, which aren't safe in folder names everywhere. Running the eval twice in one day is normal while tuning, and it must never overwrite results the writeup might use.

## `test_preflight_reports_each_problem`

**What it does:** Checks the messages `preflight` returns:
- Ollama unreachable → a single message that says how to start it;
- two missing models and an empty knowledge base → three messages, each with its exact fix (`ollama pull <model>`, `ingest_kb.py`);
- everything ready → no messages.

**Why it's there:** A missing model would otherwise surface minutes into a run as a confusing error on every scenario. Each message names its fix so the person running it doesn't have to look anything up.

## `test_main_returns_2_on_preflight_failure_and_runs_nothing`

**What it does:** When the pre-run check reports a problem, `main` prints it, returns 2, and never runs a scenario.

**Why it's there:** Exit code 2 separates "couldn't start" from a completed run with low scores (exit 0). No model time is spent when the setup is broken.

## `test_main_returns_2_on_bad_scenario`

**What it does:** A `broken.json` in the scenarios folder makes `main` print a message naming the file and return 2, before any run.

**Why it's there:** A typo in one scenario has to stop the eval up front, not be discovered after the others have run.

## `test_main_writes_runs_and_summary`

**What it does:** Runs 2 scenarios × 2 runs with model `m:1` and checks:
- the results folder is `<date>/m_1`;
- it holds 4 run files, `summary.json` (4 runs overall) and `config.json`;
- `config.json` has the model, the judge, the run count, the scenario ids and a finish time;
- each run file holds the record and its scores.

**Why it's there:** These files are what TPC-27 and TPC-28 chart and the writeup cites, so their names and contents are pinned.

## `test_run_written_before_next_starts`

**What it does:** The second run raises `KeyboardInterrupt`. The first run's file is already on disk when the interruption escapes.

**Why it's there:** A full eval takes minutes on local models. Stopping it, or a crash, must keep every run that finished.

---

## Budget and goal scenarios (TPC-26)

The `agents` fixture adds one budget and one goal file to `offline`, and stubs their runners.

## `test_kind_defaults_to_every_kind`

**What it does:** With no `--kind`, all three kinds run. `summary.json` has chat under `overall`, plus `budget` and `goal` sections, and each run file holds its named checks.

**Why it's there:** One command runs the whole benchmark. `overall` stays chat-only so it remains comparable with October's results.

## `test_kind_rejects_an_unknown_value`

**What it does:** `--kind close` exits.

**Why it's there:** A typo mustn't quietly run nothing.

## `test_kind_budget_builds_nothing_only_chat_needs`

**What it does:** `--kind budget` never builds the knowledge base or embeddings, only the agent model, and never runs a chat scenario. The summary has `overall.runs == 0` and no `goal` section, `config.json` lists only that scenario, and the table prints the false-alarm rate.

**Why it's there:** Budget runs have to work, and fail clearly, without the judge or KB being set up.

## `test_only_a_budget_scenario_also_skips_chats_needs`

**What it does:** `--only b1` doesn't build the knowledge base either.

**Why it's there:** What's needed comes from the scenarios that will run, however they were chosen.

## `test_kind_with_nothing_to_run_returns_2`

**What it does:** `--kind goal` with no goal scenarios returns 2 and says there's nothing to run.

**Why it's there:** An empty results folder would look like a run that happened.

## `test_preflight_without_chat_needs_only_the_agent_model`

**What it does:** With no knowledge base, `preflight` asks only about the agent model.

**Why it's there:** The same reason, at the check itself.

## `test_chat_summary_carries_tags`

**What it does:** A tagged chat scenario appears under `by_tag`.

**Why it's there:** Per-category rates are what the writeup reports.

## `test_skip_tag_leaves_out_tagged_scenarios`

**What it does:** `--skip-tag` can be repeated, and defaults to nothing.

**Why it's there:** TPC-27. Temporal scenarios exist before the graph does, so a baseline before the graph needs to leave them out.

## `test_new_flags_parse`, `test_defaults_keep_todays_run`, `test_label_goes_in_the_results_folder`

**What they do:** The benchmark flags parse, a run with none of them behaves as it did before, and a label goes in the folder's name.

**Why they're there:** TPC-27 runs one condition per folder, and a condition must not look like a repeat run.

## `test_graph_modes_are_refused_until_the_graph_is_built`

**What it does:** `--retrieval graph`, `hybrid` and `hybrid-shuffled` exit with code 2 until TPC-39 lands.

**Why it's there:** They would otherwise run with no graph facts and report a result about nothing.
