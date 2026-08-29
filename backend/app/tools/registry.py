"""工具注册表 — 含 agent 级 finish_task"""

from langchain_core.tools import tool

from app.tools.files import edit, glob, grep, read, write
from app.tools.shell import shell
from app.tools.subagent_tool import spawn_subagent
from app.tools.worker_tool import spawn_worker, spawn_workers

# finish_task 为 agent 级工具，由 loop 特殊处理，但也注册为 tool 供模型调用

@tool
def finish_task(message: str = "任务完成") -> str:
    """标记任务完成，结束当前 run。参数: message (总结)"""
    return f"[完成] {message}"


# 汇总 — 供 ChatOpenAI.bind_tools
TOOLS = [read, write, edit, glob, grep, shell, finish_task, spawn_subagent, spawn_worker, spawn_workers]
PLAN_TOOLS = [read, glob, grep, finish_task]

# 名称到工具的映射，用于执行分发
TOOL_MAP = {t.name: t for t in TOOLS}

# 只读工具集 (与 policy 保持一致)
READONLY_TOOLS = {"read", "glob", "grep"}
WORK_MODES = ("auto", "plan")
SPAWN_TOOLS = {"spawn_subagent", "spawn_worker", "spawn_workers"}


def normalize_work_mode(value: str | None) -> str:
    mode = (value or "auto").strip().lower()
    return mode if mode in WORK_MODES else "auto"


def tools_for_work_mode(mode: str):
    return PLAN_TOOLS if normalize_work_mode(mode) == "plan" else TOOLS
