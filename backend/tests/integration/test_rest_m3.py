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
        assert p.json()["api_key_from_env"] is False
        assert p.json().get("api_key_env") in (None, "")

        no_name = client.post(
            "/api/providers",
            json={
                "provider_id": f"{slug}e",
                "display_name": "Env Provider",
                "base_url": "https://example.invalid/v1",
                "api_key_from_env": True,
            },
        )
        assert no_name.status_code == 422

        env_p = client.post(
            "/api/providers",
            json={
                "provider_id": f"{slug}e",
                "display_name": "Env Provider",
                "base_url": "https://example.invalid/v1",
                "api_key_from_env": True,
                "api_key_env": "MY_CUSTOM_KEY",
            },
        )
        assert env_p.status_code == 200, env_p.text
        assert env_p.json()["api_key_from_env"] is True
        assert env_p.json()["api_key_env"] == "MY_CUSTOM_KEY"
        env_pid = env_p.json()["id"]
        env_probe = client.post(
            "/api/llm/probe",
            json={
                "base_url": "https://example.invalid/v1",
                "model_id": "demo-model",
                "api_key_from_env": True,
                "api_key_env": "MISSING_VAR_XYZ",
            },
        )
        assert env_probe.status_code == 200
        assert env_probe.json()["ok"] is False
        assert "MISSING_VAR_XYZ" in (env_probe.json().get("error") or "")

        missing = client.post(f"/api/providers/{pid}/test", json={})
        assert missing.status_code == 422

        t = client.post(f"/api/providers/{pid}/test", json={"model_id": "demo-model"})
        assert t.status_code == 200
        assert t.json()["ok"] is False
        assert t.json()["model"] == "demo-model"

        m = client.post(
            f"/api/providers/{pid}/models",
            json={"model_id": "demo-model", "display_name": "Demo", "context_window": 8000, "temperature": 0.1},
        )
        assert m.status_code == 200, m.text
        mid = m.json()["id"]

        mt = client.post(f"/api/models/{mid}/test")
        assert mt.status_code == 200
        assert mt.json()["ok"] is False
        assert mt.json()["model"] == "demo-model"

        probe = client.post(
            "/api/llm/probe",
            json={"base_url": "https://example.invalid/v1", "model_id": "demo-model", "api_key": "sk-unit-test"},
        )
        assert probe.status_code == 200
        assert probe.json()["ok"] is False
        assert probe.json()["model"] == "demo-model"

        client.put("/api/config/default-model", json={"default_model_id": mid})
        d = client.get("/api/config/default-model")
        assert d.json()["default_model_id"] == mid

        s = client.post("/api/sessions", json={"title": "M3 session"})
        assert s.status_code == 200, s.text
        # 新建会话应解析兜底模型
        assert s.json()["model_id"] == mid
        assert s.json().get("work_mode", "auto") == "auto"
        sid = s.json()["id"]

        patched = client.patch(f"/api/sessions/{sid}", json={"work_mode": "plan"})
        assert patched.status_code == 200, patched.text
        assert patched.json()["work_mode"] == "plan"
        got = client.get(f"/api/sessions/{sid}")
        assert got.json()["work_mode"] == "plan"
        bad = client.patch(f"/api/sessions/{sid}", json={"work_mode": "hacker"})
        assert bad.status_code == 422
        client.patch(f"/api/sessions/{sid}", json={"work_mode": "auto"})

        hist = client.get(f"/api/sessions/{sid}/messages")
        assert hist.status_code == 200
        assert hist.json() == []

        # 清理
        client.delete(f"/api/sessions/{sid}")
        client.put("/api/config/default-model", json={"default_model_id": None})
        client.delete(f"/api/models/{mid}")
        client.delete(f"/api/providers/{pid}")
        client.delete(f"/api/providers/{env_pid}")
