# Docker Dev Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docker compose up` boots the backend and frontend as two dev-mode containers on one Compose network, using Ollama on the Mac.

**Architecture:** One `docker-compose.yml` at the repo root with services `backend` (uvicorn `--reload`) and `frontend` (`next dev`). Both bind-mount the repo at `/app`. Each image installs only its dependencies; the code and data come from the mount. The frontend's `/api` proxy reaches `http://backend:8000` by service name.

**Tech Stack:** Docker 29 / Compose v5, `python:3.12-slim`, `node:25-slim`, FastAPI + uvicorn, Next.js 16.

**Spec:** `docs/superpowers/specs/2026-11-01-docker-dev-design.md`

## Global Constraints

- The compose file is the spec's `docker-compose.yml` block, verbatim, unless a ruling changes it.
- Ollama is never containerized. The backend gets `OLLAMA_BASE_URL=http://host.docker.internal:11434`.
- No app code, test or dependency changes. Running without Docker keeps working exactly as before.
- `config.py` already loads the repo-root `.env` (`/app/.env` through the mount), and `load_dotenv` doesn't override variables already set, so Compose's `environment:` values win.
- **Before any `docker compose up`:** the host servers on ports 8000 (Python) and 3000 (node) are running now. Stopping them is the user's call. Ask before killing them. Never kill them silently.
- Commits are plain messages with no `Co-Authored-By` or "Generated with Claude Code" lines (the user's rule).

## Review Focus

1. **The host servers still hold ports 8000 and 3000:** `docker compose up` must fail with a clear "port is already allocated" error, not half-start. The README tells the user to stop them first. Check in Task 3's README text.
2. **The frontend starts before the backend has finished migrating:** the first `/api` requests fail and later ones succeed with no restart. Verified in Task 2, step 3, by curling `/api/health` until it answers.
3. **The Chroma index under `kb/chroma`, written on the Mac, is read in Linux:** guidance search in chat must work in the container. Verified in Task 1, step 4.
4. **`package-lock.json` changes after the `node_modules` volume was filled:** the stale volume hides the new packages. The README gives `docker compose build frontend && docker compose down -v`. Task 3.
5. **Both the host and the container writing `dev.db` at once:** SQLite locking across the VM mount is unreliable. The README says to run one or the other. Task 3.

---

### Task 1: Backend container

**Files:**
- Create: `backend/Dockerfile`, `backend/.dockerignore`
- Create: `docker-compose.yml` (the `backend` service only for now; the `frontend` service comes in Task 2)

**Interfaces:**
- Produces: Compose service `backend` listening on `0.0.0.0:8000` inside the network, published on host port 8000. Task 2's `BACKEND_URL` targets `http://backend:8000`.

- [ ] **Step 1: Write the Dockerfile and .dockerignore** as the spec's "Dockerfiles" section says (`python:3.12-slim`, `WORKDIR /app/backend`, copy `requirements.txt`, `pip install --no-cache-dir -r requirements.txt`, no `CMD`). Ignore: `__pycache__/`, `*.db`, `logs/`, `data/`, `.pytest_cache/`.
- [ ] **Step 2: Write `docker-compose.yml`** with the spec's `backend` service.
- [ ] **Step 3: Build and start** (only after the user has stopped the host API on 8000): `docker compose up --build -d backend`. Expected: `docker compose ps` shows `backend` running. `docker compose logs backend` shows alembic's upgrade and then "Uvicorn running on http://0.0.0.0:8000".
- [ ] **Step 4: Verify:**
  - `curl -s localhost:8000/health` answers 200.
  - `docker compose exec backend python -c "import httpx; print(httpx.get('http://host.docker.internal:11434/api/tags').status_code)"` prints `200`.
  - `docker compose exec backend python -c "from app.rag.store import KnowledgeBase; print(KnowledgeBase().count())"` prints a number greater than 0 (Review Focus 3: the Mac-written index reads in Linux).
- [ ] **Step 5: Verify reload:** `touch backend/app/finance.py`. Expected: `docker compose logs --since 30s backend` shows a reload ("Reloading" / "Started server process").
- [ ] **Step 6: Run the backend tests in the container:** `docker compose exec backend python -m pytest -q -k "not live and not sandbox"`. Expected: the same count as on the host (675 passed). It proves the image's dependencies match.
- [ ] **Step 7: Commit:** `git add backend/Dockerfile backend/.dockerignore docker-compose.yml && git commit -m "build: backend dev container"`

### Task 2: Frontend container on the same network

**Files:**
- Create: `frontend/Dockerfile`, `frontend/.dockerignore`
- Modify: `docker-compose.yml` (add the spec's `frontend` service and the two named volumes)

**Interfaces:**
- Consumes: service `backend` at `http://backend:8000` (Task 1).

- [ ] **Step 1: Write the Dockerfile and .dockerignore** as the spec says (`node:25-slim`, `WORKDIR /app/frontend`, copy `package.json` and `package-lock.json`, `npm ci`, no `CMD`). Ignore: `node_modules/`, `.next/`.
- [ ] **Step 2: Add the frontend service** and the `frontend_node_modules` and `frontend_next` volumes, verbatim from the spec.
- [ ] **Step 3: Build and start** (after the user has stopped the host `next dev` on 3000): `docker compose up --build -d`. Expected: both services running. Then poll `curl -s localhost:3000/api/health` until it answers 200, within 60 s (Review Focus 2). That proves the proxy reaches `backend` over the Compose network.
- [ ] **Step 4: Verify the app:** `curl -s -o /dev/null -w "%{http_code}" localhost:3000/dashboard` prints `200`, and `curl -s localhost:3000/api/dashboard` returns the dev user's dashboard JSON.
- [ ] **Step 5: Verify reload:** `touch frontend/app/dashboard/page.tsx`. Expected: `docker compose logs --since 30s frontend` shows a recompile.
- [ ] **Step 6: Verify the host is untouched:** `ls frontend/node_modules/.bin/next` still exists, and `git status --short` shows no changes outside the new files. The container's `node_modules` and `.next` live in volumes, not the host folder.
- [ ] **Step 7: Commit:** `git add frontend/Dockerfile frontend/.dockerignore docker-compose.yml && git commit -m "build: frontend dev container on the compose network"`

### Task 3: README and the full run

**Files:**
- Modify: `README.md` (a "Run with Docker" section right after "Run locally")

- [ ] **Step 1: Write the section** with the spec's README bullets. Include Review Focus 1 (stop the host servers; the port error you'll see otherwise), 4 (`docker compose build frontend && docker compose down -v` after a lock change) and 5 (don't run the host and container servers at once).
- [ ] **Step 2: Full verification from cold:** `docker compose down`, then `docker compose up --build -d`. Repeat Task 2's steps 3 and 4. Then load http://localhost:3000 in the browser yourself if you can. Otherwise, tell the user it's the one check left to them.
- [ ] **Step 3: Leave it how the user wants it:** ask whether to leave the containers running or `docker compose down` and restart their host servers. Their choice; don't assume.
- [ ] **Step 4: Commit:** `git add README.md && git commit -m "docs: run with docker"`
