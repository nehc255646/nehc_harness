"""shell 工具 — subprocess 进程组 (M1 实现)"""

import asyncio
import logging
import os
import re
import signal
import subprocess
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from langchain_core.tools import tool

from app.core.config import settings

logger = logging.getLogger("harness.shell")


def _workdir() -> Path:
    p = Path(settings.workdir)
    if not p.is_absolute():
        p = (Path(__file__).resolve().parents[3] / settings.workdir).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


# 跟踪 shell 进程组，按 agent 分组，供 agent.stop 定向回收
_active_pgs: dict[str, set[int]] = {}

_SECRET_ENV_RE = re.compile(
    r"(API_KEY|SECRET|TOKEN|PASSWORD|PASSWD|PRIVATE_KEY|ENCRYPTION_KEY|"
    r"^REDIS_URL$|^DATABASE_URL$|^MYSQL_|^POSTGRES_|^AWS_|^OPENAI_|^ANTHROPIC_)",
    re.IGNORECASE,
)
_MAX_CAPTURE = 2_000_000


def _shell_env() -> dict[str, str]:
    """执行环境去掉密钥类变量，避免 env/printenv 把后端凭据写进 tool 结果。"""
    env = {k: v for k, v in os.environ.items() if not _SECRET_ENV_RE.search(k)}
    env["HOME"] = str(_workdir())
    env["PWD"] = str(_workdir())
    return env


@tool
def shell(command: str) -> str:
    """执行 shell 命令。参数: command (单条命令字符串)。超时由外层控制，此为同步备选。"""
    # 同步版本仅用于非 async 路径，实际 loop 中走 async 封装
    wd = _workdir()
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(wd),
            capture_output=True,
            text=True,
            timeout=settings.shell_timeout,
            start_new_session=True,
            check=False,
            env=_shell_env(),
        )
        output = (result.stdout or "") + (result.stderr or "")
        if not output:
            output = f"[退出码 {result.returncode}] (无输出)"
        else:
            # 大结果截断
            if len(output) > 8000:
                output = output[:4000] + f"\n...[截断 {len(output)-8000} 字符]...\n" + output[-4000:]
            output = f"[退出码 {result.returncode}]\n" + output
        return output
    except subprocess.TimeoutExpired:
        return f"[错误] 命令超时 ({settings.shell_timeout}s): {command[:100]}"
    except Exception as e:
        return f"[错误] 执行失败: {e}"


_PROGRESS_INTERVAL_S = 0.3
_PROGRESS_TAIL = 4000


async def shell_async(
    command: str,
    timeout: int | None = None,
    group: str | None = None,
    on_progress: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[str, int]:
    """异步 shell 执行，含进程组回收，返回 (output, returncode)。长输出经 on_progress 节流推尾部。"""
    wd = _workdir()
    timeout = timeout or settings.shell_timeout
    key = group or "global"
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=str(wd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        env=_shell_env(),
    )
    # 记录 pgid (按 agent 分组)
    pgid: int | None = None
    try:
        pgid = os.getpgid(proc.pid)  # type: ignore
        _active_pgs.setdefault(key, set()).add(pgid)
    except Exception as e:
        logger.debug("Track pgid failed: %s", e)

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    last_emit = 0.0

    async def _maybe_progress(force: bool = False) -> None:
        nonlocal last_emit
        if on_progress is None:
            return
        now = time.monotonic()
        if not force and last_emit and now - last_emit < _PROGRESS_INTERVAL_S:
            return
        last_emit = now
        raw = b"".join(stdout_chunks) + b"".join(stderr_chunks)
        tail = raw[-_PROGRESS_TAIL:].decode(errors="ignore")
        if not tail:
            return
        try:
            await on_progress(tail)
        except Exception:
            logger.debug("shell on_progress failed", exc_info=True)

    async def _pump(stream, bucket: list[bytes]) -> None:
        if stream is None:
            return
        captured = 0
        while True:
            chunk = await stream.read(2048)
            if not chunk:
                break
            if captured < _MAX_CAPTURE:
                room = _MAX_CAPTURE - captured
                bucket.append(chunk[:room])
                captured += min(len(chunk), room)
            await _maybe_progress()

    try:
        await asyncio.wait_for(
            asyncio.gather(_pump(proc.stdout, stdout_chunks), _pump(proc.stderr, stderr_chunks)),
            timeout=timeout,
        )
        await proc.wait()
        output = (b"".join(stdout_chunks).decode(errors="ignore")) + (b"".join(stderr_chunks).decode(errors="ignore"))
        if not output:
            output = f"[退出码 {proc.returncode}] (无输出)"
        else:
            if len(output) > 8000:
                output = output[:4000] + f"\n...[截断 {len(output)-8000} 字符]...\n" + output[-4000:]
            output = f"[退出码 {proc.returncode}]\n" + output
        return output, proc.returncode or 0
    except TimeoutError:
        # 超时 killpg
        try:
            if pgid:
                os.killpg(pgid, signal.SIGTERM)
            await asyncio.sleep(1)
            if proc.returncode is None and pgid:
                os.killpg(pgid, signal.SIGKILL)
        except Exception as e:
            logger.debug("Killpg failed: %s", e)
            try:
                proc.kill()
            except Exception as e2:
                logger.debug("Proc kill failed: %s", e2)
        try:
            await proc.communicate()
        except Exception as e:
            logger.debug("Communicate after timeout failed: %s", e)
        return f"[错误] 命令超时 ({timeout}s): {command[:100]}", 124
    finally:
        # 仅在进程已结束时移除登记；被取消（worker 超时/stop）时保留 pgid，
        # 交由外层 kill_shell_group 兜底回收，防孤儿进程
        if pgid and proc.returncode is not None:
            s = _active_pgs.get(key)
            if s:
                s.discard(pgid)
                if not s:
                    _active_pgs.pop(key, None)


def kill_shell_group(group: str) -> None:
    """回收指定 agent 的 shell 进程组（SIGTERM → 2s 后升级 SIGKILL，PLAN §3 语义）"""
    pgids = list(_active_pgs.get(group, ()))
    _active_pgs.pop(group, None)
    if not pgids:
        return
    for pgid in pgids:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except Exception as e:
            logger.debug("Kill shell group failed: %s", e)

    def _escalate():
        time.sleep(2)
        for pgid in pgids:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except Exception as e:
                logger.debug("Escalate SIGKILL failed: %s", e)

    # 后台线程延迟升级，不阻塞调用方也不依赖事件循环
    threading.Timer(2.0, _escalate).start()


def kill_all_shell_groups() -> None:
    """回收全部 shell 进程组 (stop_all 兜底)"""
    for group in list(_active_pgs):
        kill_shell_group(group)
