"""shell tool.progress 节流 + 取消仍保留 pgid"""

from app.tools.shell import shell_async


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
