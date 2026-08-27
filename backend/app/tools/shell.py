"""shell 工具 — subprocess 进程组 (M1 实现)"""

import asyncio
import os
import signal
import subprocess
from pathlib import Path

from langchain_core.tools import tool

from app.core.config import settings


def _workdir() -> Path:
    p = Path(settings.workdir)
    if not p.is_absolute():
        p = (Path(__file__).resolve().parents[3] / settings.workdir).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


# 跟踪当前 shell 进程组，用于 agent.stop 回收
_active_pgs: set[int] = set()


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


async def shell_async(command: str, timeout: int | None = None) -> tuple[str, int]:
    """异步 shell 执行，含进程组回收，返回 (output, returncode)"""
    wd = _workdir()
    timeout = timeout or settings.shell_timeout
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=str(wd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    # 记录 pgid
    try:
        pgid = os.getpgid(proc.pid)  # type: ignore
        _active_pgs.add(pgid)
    except Exception:
        pass

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = (stdout.decode(errors="ignore") if stdout else "") + (stderr.decode(errors="ignore") if stderr else "")
        if not output:
            output = f"[退出码 {proc.returncode}] (无输出)"
        else:
            if len(output) > 8000:
                output = output[:4000] + f"\n...[截断 {len(output)-8000} 字符]...\n" + output[-4000:]
            output = f"[退出码 {proc.returncode}]\n" + output
        return output, proc.returncode or 0
    except asyncio.TimeoutError:
        # 超时 killpg
        try:
            pgid = os.getpgid(proc.pid)  # type: ignore
            os.killpg(pgid, signal.SIGTERM)
            await asyncio.sleep(1)
            if proc.returncode is None:
                os.killpg(pgid, signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            await proc.communicate()
        except Exception:
            pass
        return f"[错误] 命令超时 ({timeout}s): {command[:100]}", 124
    finally:
        try:
            pgid = os.getpgid(proc.pid)  # type: ignore
            _active_pgs.discard(pgid)
        except Exception:
            pass


def kill_all_shell_groups():
    """agent.stop 时回收全部 shell 进程组"""
    for pgid in list(_active_pgs):
        try:
            os.killpg(pgid, signal.SIGTERM)
        except Exception:
            pass
    # 延时后 SIGKILL 由 caller 处理
