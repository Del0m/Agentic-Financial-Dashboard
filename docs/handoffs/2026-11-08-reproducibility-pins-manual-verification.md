# Manual verification: pinned Python packages and recorded model digests

What changed:
- `backend/requirements.lock` pins all 131 backend packages to the versions in the repo's venv, which the benchmark used.
- `backend/Dockerfile` and the README's venv setup install `requirements.txt` with `-c requirements.lock`.
- `eval/run_eval.py` writes `model_digests` into each run's `config.json`. `eval/budget_improvement.py` writes it into `results.json`.
- The README has a new "Reproducing the results" section listing the exact digest of each model.

## 1. The lock installs the same versions in a clean venv

```bash
python3.12 -m venv /tmp/lockcheck && /tmp/lockcheck/bin/pip install -q -r backend/requirements.txt -c backend/requirements.lock
```

```bash
diff <(/tmp/lockcheck/bin/pip freeze) <(venv/bin/pip freeze) && echo same
```

Expected: `same`.

## 2. The Docker image uses the lock

```bash
docker compose build backend
```

```bash
docker compose run --rm --no-deps backend sh -c "pip freeze | grep -E '^(chromadb|langgraph|fastapi)=='"
```

Expected: `chromadb==1.5.9`, `fastapi==0.162.0`, `langgraph==1.2.18`.

## 3. Your models match the README table

```bash
ollama list
```

Expected: each `ID` is the first 12 characters of the digest in the README's "Reproducing the results" table (for example `7cdf5a0187d5` for `qwen2.5:14b`).

## 4. An eval run records the digests

With Ollama running, run one quick chat scenario:

```bash
venv/bin/python eval/run_eval.py --kind chat --limit 1 --runs 1 --label digestcheck
```

Then open the new `eval/results/<today>/qwen2.5_14b-digestcheck/config.json`. Expected: a `model_digests` object with full digests for `qwen2.5:14b`, `nomic-embed-text` and `gemma3:12b`, matching the README. Delete that folder afterwards so it isn't committed with the real results.

## 5. Tests

```bash
cd backend && ../venv/bin/python -m pytest tests/test_eval_cli.py -q
```

Expected: all pass, including `test_model_digests_records_each_model_once_and_matches_latest` and `test_model_digests_without_ollama_says_unknown`.
