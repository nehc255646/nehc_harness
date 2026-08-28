"""M3 REST：Provider/Model/Session CRUD + 默认模型解析"""

import uuid

from fastapi.testclient import TestClient

from app.core.crypto import encryption_ready
from app.main import app


def test_rest_health():
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_provider_model_session_flow():
    if not encryption_ready():
        import pytest

        pytest.skip("ENCRYPTION_KEY missing")
    slug = f"p{uuid.uuid4().hex[:8]}"
    with TestClient(app) as client:
        if not client.get("/api/health").json().get("mysql"):
            import pytest

            pytest.skip("MySQL unavailable")
        p = client.post(
            "/api/providers",
            json={
                "provider_id": slug,
                "display_name": "Test Provider",
                "base_url": "https://example.invalid/v1",
                "api_key": "sk-unit-test",
            },
        )
        assert p.status_code == 200, p.text
        pid = p.json()["id"]
        # hello 探测允许失败
        t = client.post(f"/api/providers/{pid}/test", json={})
        assert t.status_code == 200
        assert t.json()["ok"] is False

        m = client.post(
            f"/api/providers/{pid}/models",
            json={"model_id": "demo-model", "display_name": "Demo", "context_window": 8000, "temperature": 0.1},
        )
        assert m.status_code == 200, m.text
        mid = m.json()["id"]

        client.put("/api/config/default-model", json={"default_model_id": mid})
        d = client.get("/api/config/default-model")
        assert d.json()["default_model_id"] == mid

        s = client.post("/api/sessions", json={"title": "M3 session"})
        assert s.status_code == 200, s.text
        # 新建会话应解析兜底模型
        assert s.json()["model_id"] == mid
        sid = s.json()["id"]

        hist = client.get(f"/api/sessions/{sid}/messages")
        assert hist.status_code == 200
        assert hist.json() == []

        # 清理
        client.delete(f"/api/sessions/{sid}")
        client.put("/api/config/default-model", json={"default_model_id": None})
        client.delete(f"/api/models/{mid}")
        client.delete(f"/api/providers/{pid}")
