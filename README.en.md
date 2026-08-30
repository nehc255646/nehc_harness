# Neharness

[中文](README.md) · English

Neharness is a personal, single-machine, single-user web coding agent. The main chat streams work; you open an **interactive** sub-agent from the header; the main agent can spawn **worker** sub-agents in the background. Writes and shell commands go through approval by default.

No auth, no rate limits. Binds to localhost by default. Do not expose this to the public internet.

---

## What it does

| | |
|---|---|
| Chat | Streaming text and thinking, tool cards, line-level diffs for write/edit |
| Modes | Composer **Auto** (can edit files and run commands) / **Plan** (read-only research + a plan) |
| Gate | Blacklist · `allow_rules.yaml` · session “allow similar” · first-time three-way prompt |
| Sub-agents | Interactive (sidebar; summary posted back) · workers (background, batched results) |
| Sessions | List, resume, rename; history lives in MySQL across restarts |
| Models | OpenAI-compatible provider groups; api keys Fernet-encrypted; optional `*_API_KEY` env |

“Similar” (session allow): shell uses the first two tokens of the command; other tools use the tool name. Chained commands count as similar if they start with that prefix — that is intentional.

## Stack

Backend: Python 3.14 · FastAPI · hand-written asyncio loop · LangChain. Source of truth: MySQL 8 (Alembic). Redis mirrors agent state, in-flight approvals, session allow rules, and summary cache (falls back to memory if Redis is down). Frontend: React 18 · TypeScript · Vite · Tailwind · zustand · WebSocket.

The backend **must be a single process** (`uvicorn --workers 1`). `AgentManager` is in-process; extra workers break approval routing.

## Requirements

- Python 3.14 + [uv](https://docs.astral.sh/uv/)
- Node.js 18+
- MySQL 8 (`localhost:3306`; or root `docker-compose.yml`)
- Redis (needed for reconnect / session-allow mirroring)

## Quick start

```bash
cp .env.example .env
```

Fill in at least:

| Variable | Purpose |
|---|---|
| `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` | Prefer user/database `harness`; do not run day-to-day as root |
| `ENCRYPTION_KEY` | Fernet key for provider api keys. Boot refuses if providers exist and the key is missing |

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Create the database once:

```sql
CREATE DATABASE harness CHARACTER SET utf8mb4;
CREATE USER 'harness'@'localhost' IDENTIFIED BY '...';
GRANT ALL ON harness.* TO 'harness'@'localhost';
FLUSH PRIVILEGES;
```

No local MySQL / Redis:

```bash
docker compose up -d
```

Compose publishes 3306 / 6379 on `127.0.0.1` only. Then:

```bash
chmod +x start.sh
./start.sh
```

Starts the backend on `:8000` (one worker) and the frontend on `:5173`, and tries to open a browser. Both bind to `127.0.0.1` by default.

From a host browser into a VM:

```bash
NEHARNESS_BIND=0.0.0.0 ./start.sh
```

No auth — trusted LAN only. Add the host origin to `CORS_ORIGINS`.

Manual start:

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1

# another terminal
cd frontend
npm install
npm run dev
```

Vite proxies `/api` and `/ws` to `:8000`. Open [http://127.0.0.1:5173](http://127.0.0.1:5173).

## Usage

1. Open **Models**, add an OpenAI-compatible `base_url` + `api_key` (or a `*_API_KEY` env var), then test each model (failure does not block save).
2. In the composer, switch Auto / Plan, pick a provider, then a model. Model changes apply on the next send. With no models, heuristic demo mode is used — try `执行 echo hello` / `run echo hello` to hit approval.
3. Non-allowlisted `shell` / `write` / `edit` prompts: **once** / **allow similar this session** / **reject**.
4. **Sub Agent** in the header opens the interactive sidebar (closing the panel does not stop it; the panel has a stop control). The main agent can spawn workers (bottom workspace). Header **Stop** stops the main agent and its children.
5. Files and shell are locked to `WORKDIR` (default `workspace/` in the repo). Persistent allow rules: `allow_rules.yaml`.

## Safety model

This is not a sandbox. The gate is for you, not an adversary.

- The blacklist splits on `;` `&&` `||` `|` plus a few destructive patterns such as `rm -rf`. It is not a semantic command analyzer.
- Config allow requires **every** segment to match a prefix. Session “similar” matches the **start of the whole command** (on purpose, or “allow similar” is useless).
- `read` / `write` / `edit` stay inside `WORKDIR` and do not follow outbound symlinks; `grep` does the same.
- The shell subprocess does not inherit `ENCRYPTION_KEY`, `MYSQL_*`, `*_API_KEY`, and similar secrets.
- `/api/llm/probe` only allows `http(s)`; env var names must end in `_API_KEY`.

## Configuration (excerpt)

| Variable | Default | Meaning |
|---|---|---|
| `WORKDIR` | `./workspace` | Root for files and shell |
| `HOST` | `127.0.0.1` | Backend bind; use `0.0.0.0` plus `CORS_ORIGINS` for LAN |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Browser Origin allowlist |
| `LLM_TIMEOUT` | `180` | Model request timeout (seconds) |
| `MAX_ROUNDS` | `50` | Max loop rounds per run |
| `WINDOW_N` | `20` | Sliding-window turns |
| `SUBAGENT_MAX_CONCURRENCY` | `3` | Shared sub-agent concurrency |
| `MAX_WORKERS_PER_TURN` | `2` | Workers spawned per turn |
| `APPROVAL_TIMEOUT` | `120` | Timed-out approvals are denied (seconds) |
| `WORKER_TIMEOUT` | `600` | Worker wall-clock timeout (seconds) |
| `BLACKLIST_ENABLED` | `true` | Destructive-command blacklist |
| `READONLY_NEED_APPROVAL` | `false` | Whether read-only tools need approval |
| `REDIS_URL` | `redis://localhost:6379/0` | Realtime store |

See `.env.example` for the full list.

## Development

```bash
cd backend && uv run ruff check app tests && uv run pytest
cd frontend && npx tsc --noEmit && npm run lint
```

API docs: `http://127.0.0.1:8000/docs`. Health: `GET /health`, `GET /api/health`.

Integration tests write `it_` / `ut_` sessions and `example.invalid` providers into MySQL; delete them when you are done.

## Known limits

- A process crash drops in-flight loops and approval futures; recovery is **history already in MySQL**
- Sessions share one `workspace` and may edit the same files
- Redis TTL expiry is not end-of-life; without Redis, reconnect is in-memory only
- Interactive sidebar transcripts live mostly in memory; a full reload may empty the panel even though the summary still posts back

## License

Personal project; no OSI license is declared. All rights reserved unless you add one.
