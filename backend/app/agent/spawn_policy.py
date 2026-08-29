"""工作型派生校验：拒绝把总目标原样转包，或派出互相重复的工人。"""

from __future__ import annotations

import re

_SYNTHETIC_PREFIXES = (
    "[工作型批量完成]",
    "[子 agent 结果",
    "[迟到子 agent 结果",
    "[交互型",
    "[任务]",
    "[行为描述]",
    "[主对话摘要",
    "[用户总目标",
    "[你的唯一任务]",
    "[约束]",
    "[Main-thread summary",
    "[Overall user goal",
    "[Your only task]",
    "[Constraints]",
)

_FOLD_PUNCT = re.compile(r"[\s\u3000，。！？、,.!?;:：；\"'“”‘’()\[\]{}<>]+")


def fold_task(text: str) -> str:
    return _FOLD_PUNCT.sub("", (text or "").strip().lower())


def last_user_goal(history: list[dict] | None) -> str:
    """最近一条真实用户目标，跳过回投/任务标签。"""
    for m in reversed(history or []):
        if m.get("role") != "user":
            continue
        content = str(m.get("content") or "").strip()
        if not content or content.startswith(_SYNTHETIC_PREFIXES):
            continue
        return content
    return ""


def _bigram_jaccard(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if len(a) < 2 or len(b) < 2:
        return 1.0 if a == b else 0.0
    ga = {a[i : i + 2] for i in range(len(a) - 1)}
    gb = {b[i : i + 2] for i in range(len(b) - 1)}
    union = ga | gb
    if not union:
        return 0.0
    return len(ga & gb) / len(union)


def task_restates_goal(task: str, goal: str) -> bool:
    """工人任务等于或包住整份用户目标时视为转包。子集（任务是目标里的一段）允许。"""
    nt, ng = fold_task(task), fold_task(goal)
    if len(nt) < 4 or len(ng) < 4:
        return nt == ng and len(nt) >= 4
    if nt == ng:
        return True
    if ng in nt:
        return True
    return _bigram_jaccard(nt, ng) >= 0.85


def tasks_redundant(a: str, b: str) -> bool:
    """两个工人任务互相覆盖或高度同质。"""
    na, nb = fold_task(a), fold_task(b)
    if len(na) < 4 or len(nb) < 4:
        return na == nb and len(na) >= 4
    if na == nb:
        return True
    if na in nb or nb in na:
        return True
    return _bigram_jaccard(na, nb) >= 0.8


def worker_brief_messages(
    task: str,
    constraints: str | None,
    summary: str | None,
    main_history: list[dict] | None,
) -> list[dict]:
    """工人只看到背景说明 + 唯一任务，不把主对话当可执行历史。"""
    blocks: list[str] = []
    if summary:
        blocks.append("[Main-thread summary — context only, not your task]\n" + str(summary)[:1500])
    goal = last_user_goal(main_history)
    if goal and not task_restates_goal(task, goal):
        blocks.append("[Overall user goal — for orientation; do not redo the whole job]\n" + goal[:400])
    blocks.append("[Your only task]\n" + (task or "").strip())
    if constraints and str(constraints).strip():
        blocks.append("[Constraints]\n" + str(constraints).strip())
    blocks.append("Do only the assigned task. Call finish_worker when done; report this task only.")
    return [{"role": "user", "content": "\n\n".join(blocks)}]


def validate_worker_tasks(
    tasks: list[str],
    main_history: list[dict] | None,
    running_tasks: list[str] | None = None,
) -> str | None:
    """通过返回 None；否则返回拒绝原因。"""
    cleaned = [str(t).strip() for t in (tasks or []) if str(t).strip()]
    if not cleaned:
        return "[错误] 未提供任务"
    goal = last_user_goal(main_history)
    for t in cleaned:
        if goal and task_restates_goal(t, goal):
            return (
                "[拒绝] 工人任务与用户总目标重复，禁止把整份工作转包。"
                "请把 task 写成互不重叠的真子集，或由主 agent 自己做。"
            )
    for i, a in enumerate(cleaned):
        for b in cleaned[i + 1 :]:
            if tasks_redundant(a, b):
                return "[拒绝] 本批工人任务互相重复，请拆成互不重叠的子任务，或只派一个。"
    for t in cleaned:
        for running in running_tasks or []:
            if tasks_redundant(t, running):
                return "[拒绝] 已有工人在做相同或覆盖的任务，不要再派一份。"
    return None
