"""spawn_worker / finish_worker — 工作型

主 agent 调用 spawn_* 后阻塞直到该批工人全部结束，工具结果就是结构化批次报告。
task 必须是总目标的真子集；每项必须带 done_when。mode=explore 只读。
"""

from langchain_core.tools import tool


@tool
def spawn_worker(
    task: str,
    done_when: str,
    files: list[str] | None = None,
    mode: str = "implement",
    constraints: str = "",
) -> str:
    """派生一个后台工人并等待其结束。task 必须是总目标的真子集（具体到文件或子系统）。done_when 是可验收的完成标准。files 可选，限制工人可写路径。mode=implement|explore（explore 只读）。禁止把用户原话/整份工作转包。单轮≤2，总并发≤3。派出前须已用 read/glob/grep 摸过现场。"""
    return f"[占位] spawn_worker task={task[:60]}"


@tool
def spawn_workers(
    tasks: list[str],
    done_when: list[str],
    mode: str = "implement",
    constraints: str = "",
) -> str:
    """一次派生 1-2 个互不重叠的后台工人并等待全部结束。tasks 与 done_when 等长。每项 task 必须是不同切片，禁止互相重复或等于用户总目标。mode=implement|explore。派出前须已自己只读调研。"""
    return f"[占位] spawn_workers tasks={tasks}"


@tool
def finish_worker(
    result: str,
    files_changed: list[str] | None = None,
    status: str = "done",
) -> str:
    """工作型收敛：结束后台任务。result 只覆盖本切片；files_changed 列出改过的路径；status=done|failed。"""
    return f"[占位] finish_worker result={result[:100]}"
