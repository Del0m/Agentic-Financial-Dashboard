# TPC-24: Docker and linked Plaid verification

Verified on 2026-11-02, branch `codex/tpc-24-goal-validation`, using the existing backend image and Compose source mount. Docker server: 29.3.0.

## Results

| Check | Observed result |
|---|---|
| Offline backend suite inside Docker | **807 passed, 21 deselected**, 13.78 seconds |
| Five tight-surplus trials inside Docker through `host.docker.internal:11434` | **5 passed**, 16.26 seconds; each used one model call, allocated 4859 cents, and produced a 2033-03-01 debt goal |
| Migration on a copy of the linked database | `5b7c2e9d4a10` → `7c3a1f0e2b64` succeeded |
| Existing linked data | One user, four active Plaid **Sandbox** items; 56 accounts, 240 transactions, 12 liabilities |
| Refresh through the real Plaid Sandbox API inside Docker | All four items synced successfully, 14 accounts each; counts remained 56/240/12 |
| Goal graph before and after refresh | `no_surplus`, **0 model calls**, no proposed goals |
| Existing `/goals` and `/dashboard` API handlers inside Docker | Both returned **HTTP 200** before and after refresh; `/goals` matched stored rows |
| Source database preservation | Original `backend/dev.db` SHA-256 unchanged |
| Cleanup | Disposable database/audit files removed; one-off containers removed automatically |

The linked-data command block below was executed verbatim after the initial verification and passed again, including its cleanup. The source path was also checked against the configured Compose database.

The linked history has income but a negative measured surplus after spending/saving and card minimums. Returning no goals is the correct result. This validates the linked-data no-surplus path; affordable positive-surplus proposals and retries were exercised with synthetic data in the container. This is Sandbox data, not production bank data. No financial transactions were initiated.

The checks used one-off backend containers and the actual FastAPI handlers via `TestClient`; the browser/frontend and long-running Compose services were not started. All migrations, refresh writes, goal runs, and startup bookkeeping were confined to a disposable database copy. The original linked database still has its original migration state.

## Repeat Docker tests

From the repository root with Docker Desktop and host Ollama running:

```bash
docker compose run --rm --no-deps -T -e DATABASE_URL=sqlite:////tmp/tpc24-docker-tests.db -e PROMPT_AUDIT_PATH=/tmp/tpc24-docker-audit.jsonl backend python -m pytest -q -k 'not live and not sandbox'
docker compose run --rm --no-deps -T -e DATABASE_URL=sqlite:////tmp/tpc24-docker-live.db -e PROMPT_AUDIT_PATH=/tmp/tpc24-docker-live-audit.jsonl backend python -m pytest tests/test_goal_validation_graph.py::test_live_tight_surplus_trials -q -s -rs
```

The live command must report five passes to reproduce the live result. Five skips means the configured model is unavailable from Docker; inspect host Ollama and `host.docker.internal` connectivity. Rebuild the backend image if requirements have changed.

## Repeat the linked-data check safely

From the repository root, make a consistent copy without opening the source for writes:

```bash
export TPC24_DOCKER_DIR="$(mktemp -d /tmp/tpc24-docker.XXXXXX)"
venv/bin/python - <<'PY'
import os, sqlite3
from pathlib import Path
source = Path('backend/dev.db').resolve()
target = Path(os.environ['TPC24_DOCKER_DIR']) / 'linked.db'
with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(target) as dest:
    src.backup(dest)
PY

docker compose run --rm --no-deps -T -v "$TPC24_DOCKER_DIR:/verification" -e DATABASE_URL=sqlite:////verification/linked.db -e PROMPT_AUDIT_PATH=/verification/audit.jsonl -e ENABLE_SCHEDULER=false backend sh -c 'alembic upgrade head && python -' <<'PY'
from datetime import date
from fastapi.testclient import TestClient
from sqlalchemy import select
from app import config
from app.agents.goal_graph import run_goals
from app.db.models import PlaidItem, User
from app.db.session import SessionLocal
from app.main import app
from app.plaid_service.sync import sync_item

assert config.PLAID_ENV == 'sandbox'
with SessionLocal() as s:
    users = list(s.scalars(select(PlaidItem.user_id).where(PlaidItem.access_token_enc != 'x').distinct()))
assert users, 'No linked users found'
for uid in users:
    for phase in ('stored', 'refreshed'):
        if phase == 'refreshed':
            with SessionLocal() as s:
                for item in s.scalars(select(PlaidItem).where(PlaidItem.user_id == uid,
                                                             PlaidItem.access_token_enc != 'x')).all():
                    sync_item(s, item)
        result = run_goals(SessionLocal, uid, date(2026, 11, 2))
        assert result['status'] in ('ok', 'no_surplus', 'nothing_offered', 'no_affordable_goals')
        assert result['attempts'] <= 2
        with SessionLocal() as s:
            config.DEV_USER_EMAIL = s.get(User, uid).email
        with TestClient(app) as client:
            goals = client.get('/goals')
            assert goals.status_code == client.get('/dashboard').status_code == 200
            if result['status'] == 'ok':
                assert sum(g['monthly_cents'] for g in goals.json()) <= result['cap_cents']
                assert all(g['target_date'] for g in goals.json())
            else:
                assert goals.json() == []
        print({'phase': phase, 'outcome': result['status'], 'attempts': result['attempts'],
               'goal_count': len(goals.json())})
PY

rm -f "$TPC24_DOCKER_DIR/linked.db" "$TPC24_DOCKER_DIR/audit.jsonl"
rmdir "$TPC24_DOCKER_DIR"
unset TPC24_DOCKER_DIR
```

On the inspected Sandbox dataset, both phases returned `no_surplus`, zero calls, and zero goals. Future data may produce a different outcome; any successful new proposal must still fit its computed cap and have dates. Keep the temporary directory private, and remove it after checking because it contains copied account data and encrypted link tokens.
