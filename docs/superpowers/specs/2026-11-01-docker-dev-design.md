# Docker dev setup: backend and frontend containers

Branch: `docker-dev`, cut from `TPC-32`. No Jira ticket; a developer convenience.

## Goal

Boot the app on this Mac with one command, `docker compose up`, as two containers on one Docker network: the FastAPI backend and the Next.js frontend. It runs in dev mode with live reload, the same as the two terminals in the README today.

**Who it's for:** the developer, on this Mac. Not CI or a deployment.

**Done when:**
- `docker compose up --build` serves http://localhost:3000 with the dashboard, chat (answered by Ollama on the host) and alerts working as they do today.
- `curl localhost:3000/api/health` returns the backend's health response, which proves the frontend reaches the backend over the Compose network.
- Editing a backend `.py` file reloads uvicorn, and editing a frontend `.tsx` file reloads the page, without rebuilding.

**Out of scope:**
- Ollama in a container. On a Mac, Docker has no access to the Apple GPU, so `qwen2.5:14b` would run on the CPU only.
- Production images (`next build`, no reload), Postgres, deploying anywhere.
- Changing app code, tests, or how the app runs without Docker.

## Decisions

| Decision | Why |
|---|---|
| Ollama stays native on the Mac; the backend reaches it at `http://host.docker.internal:11434` | The user's choice: GPU speed. Docker Desktop resolves `host.docker.internal` to the Mac |
| Two Compose services, `backend` and `frontend`, on Compose's default network | The user's requirement. The frontend's `/api` proxy targets `http://backend:8000` by service name |
| The whole repo is bind-mounted at `/app` in both containers | Live reload, and the containers use the existing data (`backend/dev.db`, `backend/data/*.db`, `backend/logs/`, `kb/chroma`) and the `eval/` profiles the simulation imports |
| Python dependencies are installed into the backend image; the Mac's `venv/` is never used inside it | `venv/` holds macOS binaries |
| The frontend's `node_modules` and `.next` are named volumes over the mount | Native packages (Turbopack, Tailwind's oxide, lightningcss) are platform-specific, and the Mac's `.next` cache would clash with Linux's |
| `WATCHPACK_POLLING=true` for the frontend; uvicorn's default watcher for the backend | File events over the macOS bind mount are unreliable for Next's watcher. Uvicorn's `watchfiles` notices changes on the mount |
| The backend runs `alembic upgrade head` before starting uvicorn | A fresh `dev.db` gets its schema, and an existing one picks up new migrations, as in the README's first-time steps |
| `.env` at the repo root via `env_file`; Compose only overrides `OLLAMA_BASE_URL` and sets `BACKEND_URL` | One config file for both ways of running |
| Images: `python:3.12-slim` and `node:25-slim` | Match the host's Python 3.12.14 and Node 25.9.0 |
| Both ports published: 8000 (API, `/docs`) and 3000 (app) | Same URLs as today. The host servers must be stopped first |

## Files

**New:**
- `docker-compose.yml`
- `backend/Dockerfile`, `backend/.dockerignore`
- `frontend/Dockerfile`, `frontend/.dockerignore`

**Changed:**
- `README.md`: a "Run with Docker" section.

## `docker-compose.yml`

```yaml
services:
  backend:
    build: ./backend
    working_dir: /app/backend
    command: sh -c "alembic upgrade head && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
    env_file: .env
    environment:
      OLLAMA_BASE_URL: http://host.docker.internal:11434
    volumes:
      - .:/app
    ports:
      - "8000:8000"

  frontend:
    build: ./frontend
    working_dir: /app/frontend
    command: npm run dev -- -H 0.0.0.0
    environment:
      BACKEND_URL: http://backend:8000
      WATCHPACK_POLLING: "true"
    volumes:
      - .:/app
      - frontend_node_modules:/app/frontend/node_modules
      - frontend_next:/app/frontend/.next
    ports:
      - "3000:3000"
    depends_on:
      - backend

volumes:
  frontend_node_modules:
  frontend_next:
```

`depends_on` only orders start-up. The frontend proxies per request, so a backend still starting just fails those first requests.

## Dockerfiles

**`backend/Dockerfile`:** `FROM python:3.12-slim`, `WORKDIR /app/backend`, copy `requirements.txt`, `pip install --no-cache-dir -r requirements.txt`. The code arrives through the mount, so no `COPY` of the app is needed for dev. No `CMD`; Compose sets the command.

**`frontend/Dockerfile`:** `FROM node:25-slim`, `WORKDIR /app/frontend`, copy `package.json` and `package-lock.json`, `npm ci`. Compose mounts the `frontend_node_modules` volume over `/app/frontend/node_modules`. On first use Docker fills the volume from the image's installed modules. A later `package-lock.json` change needs `docker compose build frontend` and dropping the volume (`docker compose down -v`), which the README says.

**`.dockerignore`:** keep `venv/`, `__pycache__/`, `*.db`, `logs/`, `data/` out of the backend build context, and `node_modules/` and `.next/` out of the frontend's. Each image only copies its dependency manifests, so this mostly keeps builds fast.

## README

A "Run with Docker" section after "Run locally":
- Start Ollama on the Mac first (`ollama serve`, with the models pulled).
- Stop the host servers on ports 8000 and 3000.
- `docker compose up --build`, then open http://localhost:3000.
- One-off commands run in the backend container: `docker compose exec backend python scripts/seed_budget_user.py ...` and `docker compose exec backend python -m pytest`. Inside the container it's `python`, not `../venv/bin/python`.
- After changing `requirements.txt` or `package-lock.json`: `docker compose build`. For the frontend, also `docker compose down -v` to refresh the `node_modules` volume.

## Verification

1. `docker compose up --build -d` builds both images, and both containers stay up.
2. `curl -s localhost:8000/health` and `curl -s localhost:3000/api/health` both answer.
3. `docker compose exec backend python -c "import httpx; print(httpx.get('http://host.docker.internal:11434/api/tags').status_code)"` prints 200.
4. `curl -s localhost:3000/dashboard` returns the page (HTTP 200).
5. Touching a backend file shows a reload in `docker compose logs backend`. Touching a frontend file shows a recompile in `docker compose logs frontend`.
6. `docker compose down` stops both. The repo's data files are unchanged except for what the app itself writes.

## Risks

| Risk | Mitigation |
|---|---|
| The host servers already hold ports 8000 and 3000 | The README says to stop them first; `docker compose up` fails clearly on a port clash |
| SQLite on a bind mount while a host process also writes it | Run one or the other, never both at once. The README says so |
| A Python package with no Linux wheel for the architecture (arm64) | `python:3.12-slim` is multi-arch; if a build fails on a missing wheel, add the build tools it names to the backend image |
| Polling raises CPU use in the frontend container | Only the frontend polls, and only in dev |
