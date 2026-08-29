# Agent Harness

[中文](README.md) · English

A personal, single-machine, single-user, multi-session web coding agent. The main agent can keep working and, on demand, spawn an **interactive** sub-agent (sidebar chat) or a **worker** sub-agent (background, concurrent). File and shell tools go through a user approval gate by default.

> You own the safety model: the first non-allowlisted command prompts a three-way approval. The blacklist is a backstop, not a sandbox.

---

## Features

- Streaming main chat, tool-call cards, line-level diffs for write/edit
- Permission gate: blacklist, config allowlist, session “allow similar”, first-time approval
- Interactive sub-agents: sidebar conversation; a summary is posted back to the main agent
- Worker sub-agents: background concurrency, same tools as the main agent, batched results (max 2 per turn, 3 concurrent total)
- Session list, resume, rename; history survives backend restarts
- Provider/model groups (OpenAI-compatible APIs); api keys stored encrypted
- WebSocket reconnect: in-flight approvals and session allow rules survive refresh (Redis required)
- Throttled `tool.progress` tails for long commands; accent themes (cyan / emerald / violet / amber)

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.14 · FastAPI · hand-written asyncio loop · LangChain (tools / `ChatOpenAI`) |
| Persistence | MySQL 8 (source of truth) · Alembic |
| Realtime | Redis (agent state, pending approvals, session allow rules, summary cache) |
| Frontend | React 18 · TypeScript · Vite · Tailwind · zustand · WebSocket |

Hard constraint: the backend **must be a single process** (`uvicorn --workers 1`). `AgentManager` is an in-process registry; multiple workers break approval routing.

## Requirements

- Python 3.14 + [uv](https://docs.astral.sh/uv/)
- Node.js 18+
- MySQL 8 (`localhost:3306`; use root `docker-compose.yml` if you have no local instance)
- Redis (missing Redis only logs a warning and falls back to memory; reconnect / session allow-rule mirroring need Redis)

## Quick start

```bash
cp .env.example .env
```

Fill in at least:

| Variable | Purpose |
|---|---|
| `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` | Prefer database/user `harness`; do not run day-to-day as root |
| `ENCRYPTION_KEY` | Fernet key used to encrypt provider api keys |

Generate a key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Create the database once (example):

```sql
CREATE DATABASE harness CHARACTER SET utf8mb4;
CREATE USER 'harness'@'localhost' IDENTIFIED BY '...';
GRANT ALL ON harness.* TO 'harness'@'localhost';
FLUSH PRIVILEGES;
```

If you have no local MySQL/Redis:

```bash
docker compose up -d
```

Start (recommended):

```bash
chmod +x start.sh
./start.sh
```

This starts the backend on `:8000` (one worker) and the frontend on `:5173`, and tries to open a browser. On a headless VM, open `http://<VM_IP>:5173` from the host (allow 5173 and 8000).

Manual start:

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1

# another terminal
cd frontend
npm install
npm run dev -- --host
```

Vite proxies `/api` and `/ws` to `:8000`. Open `http://localhost:5173`.

## Usage

1. Open **Models**, add an OpenAI-compatible provider (`base_url` + `api_key`), then test each model individually (failure does not block save).
2. In the composer, pick a provider then a model; the change applies on the next send. With no models configured, heuristic demo mode is used (try `执行 echo hello` / `run echo hello` to hit approval).
3. Non-allowlisted `shell` / `write` / `edit` prompts: **once** / **allow similar this session** / **reject**. “Similar” for shell is the first two tokens; for other tools it is the tool name.
4. The main agent can spawn interactive (sidebar) or worker (bottom bar) sub-agents. Closing a panel does not stop the sub-agent.
5. The working directory is locked to `WORKDIR` (default `workspace/` in the repo). Persistent allow rules live in `allow_rules.yaml`.

## Configuration (excerpt)

| Variable | Default | Meaning |
|---|---|---|
| `WORKDIR` | `./workspace` | Root for files and shell |
| `MAX_ROUNDS` | 50 | Max loop rounds per run |
| `WINDOW_N` | 20 | Sliding-window turns |
| `SUBAGENT_MAX_CONCURRENCY` | 3 | Shared sub-agent concurrency |
| `MAX_WORKERS_PER_TURN` | 2 | Workers spawned per turn |
| `APPROVAL_TIMEOUT` | 120s | Timed-out approvals are denied |
| `WORKER_TIMEOUT` | 600s | Worker wall-clock timeout |
| `BLACKLIST_ENABLED` | true | Minimal destructive-command blacklist |
| `READONLY_NEED_APPROVAL` | false | Whether read-only tools need approval |
| `REDIS_URL` | `redis://localhost:6379/0` | Realtime store |

See `.env.example` for the full list.

## Development

```bash
cd backend && uv run ruff check app tests && uv run pytest
cd frontend && npx tsc --noEmit && npm run lint
./scripts/demo.sh
```

API docs: `http://localhost:8000/docs` once the backend is up. Health: `GET /health`, `GET /api/status`.

## Known limits

- A process crash drops in-flight loops and approval futures; recovery is **history already written to MySQL**
- Sessions share one `workspace` and may edit the same files
- Single user, no auth, no rate limits — do not expose this to the public internet
- The blacklist splits on `;` / `&&` / pipes; it is not a semantic command analyzer
- Redis TTL expiry is not an end-of-life signal; without Redis, reconnect falls back to in-memory state

## License

Personal project; no OSI license is declared. All rights reserved unless you add one.
