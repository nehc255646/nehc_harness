"""spawn_worker / finish_worker — 工作型 (M2)

约束（防全量转包）：工具描述 + 独立 prompt 双重约束 — 仅当任务可拆为独立子任务且并行收益明显时才派生；单轮 ≤2，总并发 ≤3，主 agent 保留编排职责。
"""

from langchain_core.tools import tool


@tool
def spawn_worker(task: str, constraints: str = "") -> str:
    """派生一个后台工人。task 必须是总目标的真子集（具体到文件或子系统），禁止把用户原话/整份工作转包。派出后主 agent 不要再做同一件事。单轮≤2，总并发≤3。"""
    return f"[占位] spawn_worker task={task[:60]}"


@tool
def spawn_workers(tasks: list[str], constraints: str = "") -> str:
    """一次派生 1-2 个互不重叠的后台工人。每项 task 必须是不同切片，禁止互相重复或等于用户总目标。派出后主 agent 等待回投再聚合。"""
    return f"[占位] spawn_workers tasks={tasks}"


@tool
def finish_worker(result: str) -> str:
    """工作型收敛：结束后台任务并回投结果。参数: result(结构化摘要)"""
    return f"[占位] finish_worker result={result[:100]}"
