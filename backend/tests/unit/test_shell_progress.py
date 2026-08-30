"""shell tool.progress 节流 + 取消仍保留 pgid"""

from app.tools.shell import _shell_env, shell_async


async def test_shell_progress_emits_tail():
    tails: list[str] = []

    async def on_progress(tail: str):
        tails.append(tail)

    cmd = "bash -c 'for i in 0 1 2 3; do echo $i; sleep 0.35; done'"
    out, code = await shell_async(cmd, timeout=8, on_progress=on_progress)
    assert code == 0
    assert "3" in out
    assert tails, "长输出应按节流推送尾部"
    assert any("0" in t or "1" in t for t in tails)


def test_shell_env_strips_secrets(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "fernet-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("MYSQL_PASSWORD", "db-secret")
    env = _shell_env()
    assert "ENCRYPTION_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert "MYSQL_PASSWORD" not in env
    assert "PATH" in env


async def test_shell_async_does_not_leak_secrets(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "fernet-secret-value")
    out, code = await shell_async("printenv ENCRYPTION_KEY || true", timeout=8)
    assert code == 0
    assert "fernet-secret-value" not in out
