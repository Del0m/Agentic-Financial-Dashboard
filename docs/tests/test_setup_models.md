# Tests: `backend/tests/test_setup_models.py`

These tests cover `backend/scripts/setup_models.py` (TPC-42). It pulls the Ollama models the app needs, then builds the knowledge base if it's empty.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_setup_models.py -v
```

## Shared setup

- `FakeOllama` stands in for the Ollama client. It can already have models, be down, or fail a pull.
- An autouse fixture sets the model names, removes the wait for Ollama, and replaces the knowledge base steps with counters.

## `test_pulls_both_models_then_builds_the_kb`

**What it does:** Starts with no models and an empty knowledge base. It checks that both models are pulled, chat model first, and that the knowledge base is built.

**Why it's there:** This is the normal first start.

## `test_models_already_there_are_skipped`

**What it does:** Ollama already lists `chat:14b` and `embed:latest`, while the settings say `chat:14b` and `embed`. It checks that nothing is pulled.

**Why it's there:** Ollama reports an untagged name as `:latest`. Without matching that, every start would contact the registry, and fail when offline.

## `test_a_built_kb_is_not_rebuilt`

**What it does:** Starts with a knowledge base that has chunks, and checks that nothing is fetched or ingested.

**Why it's there:** The script runs on every start, and rebuilding means embedding every chunk again.

## `test_ollama_down_says_how_to_fix_it`

**What it does:** Makes Ollama unreachable. It checks for exit code 1 and a message naming Ollama and the command to run again.

**Why it's there:** Forgetting to start Ollama is the likeliest first-run mistake, and this message is the only guidance the viewer gets.

## `test_a_failed_pull_stops_before_the_kb`

**What it does:** Makes the embedding model's pull fail. It checks for exit code 1, the model's name in the output, and no ingest.

**Why it's there:** Ingesting needs that model, so carrying on would only add a more confusing error.

## `test_a_failed_page_fetch_still_builds_the_kb`

**What it does:** Makes the page fetch fail. It checks that the knowledge base is still built and that the output says a page is missing.

**Why it's there:** One copyrighted page isn't in the repo and comes from the network. The other 15 sources are enough for the demo.
