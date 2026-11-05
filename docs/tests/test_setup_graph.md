# test_setup_graph.py

Tests for `scripts/setup_graph.py`, which fills the temporal graph during `docker compose up`. None needs a model.

## `test_loads_guidance_and_each_users_history`

**What it does:** After the script runs, a question about the 2024 Roth IRA limit finds its figure, and a question about the gym finds the user's charges.

**Why it's there:** Under Docker the graph stayed empty, so chat answered without it. The setup scripts never loaded the guidance, and a user seeded earlier had no history until a daily run.

## `test_running_again_changes_nothing`, `test_a_finished_history_isnt_rebuilt`

**What they do:** A second run leaves the current facts as they are, and doesn't rebuild a history that's finished.

**Why they're there:** Every `docker compose up` runs the script.

## `test_missing_guidance_still_builds_histories`, `test_unreadable_guidance_doesnt_stop_startup`, `test_one_users_failure_doesnt_stop_the_others`

**What they do:** A missing or broken `kb/graph.json`, or a failure on one user, is reported. The script still exits 0 and carries on with the rest.

**Why they're there:** The backend waits for `init` to succeed, and the app works without the graph, so a graph problem mustn't stop it starting.
