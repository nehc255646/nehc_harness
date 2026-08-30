# Neharness backend

See the repository [README](../README.md) ([English](../README.en.md)).

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```
