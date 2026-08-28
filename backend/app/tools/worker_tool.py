"""spawn_worker / finish_worker — 工作型 (M2)

约束（防全量转包）：工具描述 + 独立 prompt 双重约束 — 仅当任务可拆为独立子任务且并行收益明显时才派生；单轮 ≤2，总并发 ≤3，主 agent 保留编排职责。
"""

from langchain_core.tools import tool


@tool
def spawn_worker(task: str, constraints: str = "") -> str:
    """派生后台工作型子 agent（与主 agent 同等工具权限，后台并发）。参数: task(子任务描述，必填), constraints(可选约束)。约束：仅当任务可拆为独立子任务且并行收益明显时才派生；单轮不超过2个，总并发不超过3。"""
    return f"[占位] spawn_worker task={task[:60]}"


@tool
def spawn_workers(tasks: list[str], constraints: str = "") -> str:
    """批量派生后台工作型（同 spawn_worker，支持一次派生1-2个独立子任务）。参数: tasks(任务列表 1-2 项), constraints(可选约束)。"""
    return f"[占位] spawn_workers tasks={tasks}"


@tool
def finish_worker(result: str) -> str:
    """工作型收敛：结束后台任务并回投结果。参数: result(结构化摘要)"""
    return f"[占位] finish_worker result={result[:100]}"
