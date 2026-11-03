# test_kg_facts.py

Tests for TPC-37: what the temporal graph adds to the budget agent. None needs a model.

## `test_a_merchant_from_last_december_shows_as_seasonal_history`, `test_a_rule_this_month_shows_as_a_move_with_its_amount`, `test_a_price_rise_shows_as_a_price_change`, `test_dismissed_cuts_show_in_the_advice_history`, `test_one_dismissal_isnt_enough`

**What they do:** Each of the four planted users produces the fact only the graph can give it: a merchant active last December, a rule's move and amount, a price rise, and two dismissed cuts.

**Why they're there:** If the fact isn't there, the model can't use it, and the ablation would measure nothing.

## `test_with_the_graph_off_prompts_and_schema_are_unchanged`, `test_with_the_graph_on_the_prompts_add_to_the_old_ones`, `test_graph_facts_reach_the_prompt_only_when_on`, `test_the_four_existing_planted_users_are_unchanged_with_the_graph_off`

**What they do:** With `KG_MODE` off, the prompts and schema match what they were before, byte for byte, and no graph facts reach the model.

**Why they're there:** This is the ablation's control. A difference between off and on has to come from the graph.

## `test_graph_facts_leave_out_account_details`

**What it does:** The prompt has no account ids or real user id.

**Why it's there:** Graph facts go through the same privacy gateway as the rest.

## `test_reclassified_can_only_alert`, `test_a_cut_dismissed_twice_isnt_proposed_again`, `test_with_the_graph_off_the_guard_doesnt_apply`

**What they do:** Code stops a cut or new limit on a moved category, and stops a third proposal of a dismissed cut, even when the model picks one. With the graph off, the second guard doesn't apply.

**Why they're there:** A moved category isn't overspending, and the guard depends on the graph, so the ablation can see its effect.
