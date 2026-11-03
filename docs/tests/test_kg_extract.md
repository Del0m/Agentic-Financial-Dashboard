# test_kg_extract.py

Tests for TPC-38: checking what the model extracts from the guidance, and loading it. None needs a model.

## `test_a_limit_keeps_its_tax_year_as_a_validity_interval`, `test_a_limit_that_cant_be_checked_is_dropped`, `test_a_limit_whose_year_is_far_from_its_amount_is_dropped`, `test_an_amount_doesnt_match_inside_a_bigger_number`

**What they do:** A limit needs a tax year and an amount that are both in the chunk, close together. Anything else is dropped.

**Why they're there:** A limit in the wrong year is worse than none. The IRS chart mixes several years, which is the problem this graph exists to fix.

## `test_an_unknown_concept_is_logged_and_never_added`, `test_a_rule_needs_text`, `test_a_concept_cant_relate_to_itself`, `test_mentions_are_whole_words`, `test_merge_keeps_the_first_and_reports_a_disagreement`

**What they do:** Only listed concepts get in, rules need text, and disagreements between chunks are reported for review.

**Why they're there:** The same concept can't appear under two names, and a person reads the disagreements before committing `kb/graph.json`.

## `test_each_year_is_its_own_edge_and_as_of_returns_the_right_one`, `test_reloading_adds_nothing_and_a_changed_fact_is_corrected`, `test_mentioned_in_points_at_a_source`

**What they do:** Each tax year is a separate edge that `as_of` finds by date, a reload writes nothing new, and a changed fact is corrected with its history kept.

**Why they're there:** `ingest_kb.py` reloads this on every rebuild, and the year-specific limits are TPC-38's done-when.

## `test_committed_concepts_load_and_cover_the_bridges`, `test_aliases_arent_shared_between_concepts`

**What they do:** The committed list has at least 40 concepts, includes every key TPC-36's bridges use, and no name belongs to two concepts.

**Why they're there:** A bridge to a missing concept would silently point at nothing.

## `test_a_limit_must_go_from_an_account_to_a_kind_of_limit`

**What it does:** A limit's subject has to be an account or plan, and its object a kind of limit.

**Why it's there:** The first extraction stored IRA limits as `contribution-limit -> income-limit`, which said nothing about whose limit they were.

## `test_review_rejects_corrects_and_adds`, `test_a_review_entry_matching_nothing_fails_loudly`

**What they do:** The recorded review changes a copy of the raw output, and an entry that matches nothing is an error.

**Why they're there:** What the model produced and what review changed have to stay separate, so precision and recall score the model.

## `test_committed_graph_has_year_specific_limits`

**What it does:** Loads the committed `kb/graph.json` and checks `as_of` returns each year's IRA limit.

**Why it's there:** TPC-38's done-when. The 2025 401(k) limit isn't in the knowledge base, so the graph can't have it.
