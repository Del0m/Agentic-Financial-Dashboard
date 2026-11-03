# test_kg_project.py

Tests for TPC-36: projecting a user's rows into the graph. None needs a model.

## `test_simulating_a_month_twice_gives_the_same_graph`, `test_a_rule_made_mid_month_opens_its_edge_from_that_date`, `test_accepting_a_proposal_keeps_the_pending_belief_in_history`

**What they do:** The ticket's three done-when conditions. Two simulations of `overspend-recurring` hold the same current facts, a rule's edge starts on the day it's made, and accepting a proposal changes its outcome while the pending version stays in audit history.

**Why they're there:** The graph has to be repeatable for the benchmark, and it has to answer "what did the agent know then?".

## `test_spends_at_nets_refunds_and_bounds_the_month`, `test_spends_at_skips_income_and_no_merchant`, `test_spends_at_takes_the_category_with_the_most_spending`

**What they do:** Check the pure projection of one merchant's month.

**Why they're there:** `SPENDS_AT` feeds the seasonal check in TPC-37, and it must count money the way budgets do.

## `test_a_changed_month_corrects_the_edge_and_keeps_the_old_belief`, `test_running_twice_adds_nothing`, `test_recategorizing_a_past_month_corrects_it`, `test_a_merchant_with_nothing_left_goes_to_zero`, `test_a_backdated_month_is_skipped_and_reported`

**What they do:** A changed total corrects its edge, an unchanged run writes nothing, a recategorized past month keeps its dates, and a backdated insert is reported, not raised.

**Why they're there:** These are the store's rules, as the handoff from TPC-35 sets them out.

## `test_replacing_and_deleting_a_rule`, `test_a_price_change_closes_the_stream_edge_and_opens_a_new_one`, `test_a_stale_stream_ends_its_edge`, `test_advice_with_no_category_has_no_edge`, `test_a_bridge_points_at_a_shared_concept`

**What they do:** Check each other relation's changes over time, and that a bridge reaches a shared node.

**Why they're there:** Each is a row in the table in architecture 6b.1.

## `test_a_failed_projection_rolls_back_only_itself`, `test_a_graph_failure_doesnt_stop_the_budget_run`

**What they do:** A failure leaves other work in the transaction alone, and a run still finishes and records `graph_error`.

**Why they're there:** An overspend alert matters more than the graph.

## `test_close_projects_the_final_month_and_the_new_limits`, `test_backfill_script_builds_a_seeded_users_history`, `test_build_kg_script`

**What they do:** Close writes the closed month and the new period's limits, and a backfill records every month with nothing skipped, directly and through the script.

**Why they're there:** Temporal questions depend on history being there.
