# test_kg_retrieve.py

Tests for TPC-39: chat retrieval from the graph. None needs a model.

## `test_read_time`

**What it does:** Reads the date a question is about, by rule: a year, last year, last month, a named month or "since" a month. A month later than today means last year's.

**Why it's there:** Asking about 2024 has to get 2024's limit.

## `test_a_limit_question_gets_that_years_number`, `test_a_year_the_guidance_doesnt_cover_gets_no_limit`, `test_a_temporal_question_finds_its_fact`

**What they do:** Each temporal scenario finds the fact its golden answer needs, and a question about 2030 finds no limit.

**Why they're there:** The ticket's done-when, and TPC-49's failure. The model can only use what retrieval gives it.

## `test_user_facts_are_audited_with_the_graph_purpose`, `test_guidance_alone_isnt_audited`

**What they do:** Facts about the user go through the gateway and its audit log, with no real user id. Public guidance skips it, like `search_kb`.

**Why they're there:** Every prompt holding user data has to be audited.

## `test_shuffled_facts_keep_their_length_and_change_their_values`

**What it does:** `hybrid-shuffled` has the same values at the same total length on different facts, and repeats with its seed.

**Why it's there:** It's the control that separates the value of the facts from the effect of a longer prompt.

## `test_facts_reach_the_prompt_with_their_date`, `test_vector_mode_sends_no_graph_facts`, `test_get_history_tool_only_in_graph_modes`

**What they do:** Facts appear under "Facts (valid as of <date>)" in graph modes only, and `get_history` exists only there.

**Why they're there:** A condition without the graph can't be allowed to reach it.

## `test_a_roth_ira_finds_the_limit_on_the_ira_it_is_a_kind_of`, `test_a_year_with_no_limit_says_so`, `test_a_price_change_is_stated_with_its_month`, `test_a_steady_charge_states_no_price_steps`, `test_advice_dates_read_as_words`

**What they do:** A Roth IRA reaches its parent's limits, a year with no limit is stated as a fact, a monthly charge's price changes are listed, and dates read as words.

**Why they're there:** Each came from a live failure. These fixes were made after seeing the temporal scenarios fail, so the paper has to say the graph was tuned on them.

## `test_get_history_is_left_out_when_facts_already_answer`, `test_get_history_is_offered_when_there_are_no_facts`

**What they do:** `get_history` is only offered on a turn where retrieval found no facts.

**Why they're there:** Offered the tool with the answer already in the facts, `qwen2.5:14b` wrote empty answers or answered in Thai, the same way every run. The same prompt with the tool left out answered correctly.
