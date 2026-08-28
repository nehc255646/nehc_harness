# Harness backend

See the repository [README](../README.md) ([English](../README.en.md)).

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```
