# Docker graph startup: manual verification

`docker compose up` now fills the temporal graph, through a new `init` step, `backend/scripts/setup_graph.py`.

## 1. Boot

```bash
docker compose up -d --build
```

```bash
docker compose logs init --tail 8
```

Expect `guidance graph: 125 facts, 0 skipped`. The first boot of a user's history also prints `graph for <user>: N facts, 0 skipped`.

## 2. Run it again

```bash
docker compose up -d
```

```bash
docker compose logs init --tail 4
```

Expect the guidance line again and no `graph for` lines, because finished histories aren't rebuilt.

## 3. In the app

Open http://localhost:3000/chat. Each answer should carry the `graph` chip and be in English.

| Ask | Expect |
|---|---|
| "What was the Roth IRA contribution limit in 2024?" | $7,000, or $8,000 at age 50 or older |
| "When did my gym membership start?" | September 2026 |

## 4. A problem doesn't stop startup

Temporarily rename `kb/graph.json`, then run `docker compose up -d`. Expect `no graph.json, so the guidance graph wasn't loaded`, and the app still opens. Rename it back and run `docker compose up -d` again.

## 5. Fresh clone

Clone the repo somewhere new, stop any running stack, and run `docker compose up --build`. Expect `init` to create `.env`, run all migrations, seed the demo user and load the guidance. `models` needs Ollama running, and downloads about 9 GB if the models aren't there yet.
