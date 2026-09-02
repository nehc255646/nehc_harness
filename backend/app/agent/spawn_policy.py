"""工作型派生校验：先调研、带验收标准、拒绝整单转包与假拆分。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_SYNTHETIC_PREFIXES = (
    "[工作型批量完成]",
    "[系统·工人批次完成]",
    "[系统·迟到工人结果",
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
    "[Done when]",
    "[Allowed files]",
)

_FOLD_PUNCT = re.compile(r"[\s\u3000，。！？、,.!?;:：；\"'“”‘’()\[\]{}<>]+")
_READONLY_TOOLS = frozenset({"read", "glob", "grep"})
_WORKER_MODES = frozenset({"implement", "explore"})


@dataclass
class WorkerSpec:
    task: str
    done_when: str
    files: list[str] = field(default_factory=list)
    mode: str = "implement"


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


def history_has_readonly_explore(history: list[dict] | None) -> bool:
    """主 agent 是否已经成功用过 read/glob/grep。"""
    for m in history or []:
        if m.get("role") != "tool":
            continue
        if str(m.get("name") or "") not in _READONLY_TOOLS:
            continue
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        if content.startswith(("[错误]", "[拒绝]", "[超时]", "[中断]", "[异常]")):
            continue
        return True
    return False


def _norm_files(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, list):
                out.extend(_norm_files(item))
                continue
            text = str(item or "").strip()
            if text:
                out.append(text)
        return out
    return []


def _norm_mode(value) -> str:
    mode = str(value or "implement").strip().lower()
    return mode if mode in _WORKER_MODES else "implement"


def _item_to_spec(item, default_mode: str) -> tuple[WorkerSpec | None, str | None]:
    if isinstance(item, str):
        task = item.strip()
        return None, "[拒绝] 每项工人任务必须带 done_when（可验收的完成标准）。" if task else "[错误] 未提供任务"
    if not isinstance(item, dict):
        return None, "[错误] 工人任务格式无效"
    task = str(item.get("task") or "").strip()
    done_when = str(item.get("done_when") or "").strip()
    if not task:
        return None, "[错误] 工人任务缺少 task"
    if len(fold_task(done_when)) < 4:
        return None, "[拒绝] 每项工人任务必须带 done_when（可验收的完成标准）。"
    return (
        WorkerSpec(
            task=task,
            done_when=done_when,
            files=_norm_files(item.get("files")),
            mode=_norm_mode(item.get("mode") or default_mode),
        ),
        None,
    )


def parse_worker_specs(name: str, args: dict | None) -> tuple[list[WorkerSpec], str | None]:
    """从 spawn_worker / spawn_workers 参数解析出 WorkerSpec 列表。"""
    args = args or {}
    default_mode = _norm_mode(args.get("mode"))
    items: list = []
    if name == "spawn_worker":
        items = [args]
    elif name == "spawn_workers":
        raw_items = args.get("items")
        if isinstance(raw_items, list) and raw_items:
            items = raw_items
        else:
            tasks = args.get("tasks") or []
            if not isinstance(tasks, list):
                return [], "[错误] spawn_workers 需提供 tasks 数组"
            done_when = args.get("done_when")
            files = args.get("files")
            for i, t in enumerate(tasks):
                if isinstance(t, dict):
                    items.append(t)
                    continue
                item = {"task": t, "mode": default_mode, "files": files}
                if isinstance(done_when, list):
                    if i < len(done_when):
                        item["done_when"] = done_when[i]
                elif done_when:
                    item["done_when"] = done_when
                items.append(item)
    else:
        return [], f"[错误] 未知 spawn 工具: {name}"

    specs: list[WorkerSpec] = []
    for item in items:
        spec, err = _item_to_spec(item, default_mode)
        if err:
            return [], err
        if spec:
            specs.append(spec)
    if not specs:
        return [], "[错误] 未提供任务"
    return specs, None


def path_in_scope(path: str, files: list[str] | None) -> bool:
    """write/edit 路径是否落在工人声明的 files 内。files 空则不限制。"""
    if not files:
        return True
    raw = (path or "").strip()
    if not raw:
        return False
    p = raw.replace("\\", "/").lstrip("./")
    try:
        p = str(Path(p).as_posix()).lstrip("/")
    except Exception:
        p = raw.replace("\\", "/")
    for f in files:
        ff = (f or "").strip().replace("\\", "/").lstrip("./")
        if not ff:
            continue
        try:
            ff = str(Path(ff).as_posix()).lstrip("/")
        except Exception:
            ff = ff.replace("\\", "/")
        if p == ff:
            return True
        if p.startswith(ff.rstrip("/") + "/"):
            return True
        if ff.startswith(p.rstrip("/") + "/"):
            return True
    return False


def worker_brief_messages(
    task: str,
    constraints: str | None,
    summary: str | None,
    main_history: list[dict] | None,
    done_when: str | None = None,
    files: list[str] | None = None,
    mode: str = "implement",
) -> list[dict]:
    """工人只看到背景说明 + 唯一任务，不把主对话当可执行历史。"""
    blocks: list[str] = []
    mode = _norm_mode(mode)
    if summary:
        blocks.append("[Main-thread summary — context only, not your task]\n" + str(summary)[:1500])
    goal = last_user_goal(main_history)
    if goal and not task_restates_goal(task, goal):
        blocks.append("[Overall user goal — for orientation; do not redo the whole job]\n" + goal[:400])
    blocks.append("[Your only task]\n" + (task or "").strip())
    if done_when and str(done_when).strip():
        blocks.append("[Done when]\n" + str(done_when).strip())
    if files:
        blocks.append("[Allowed files]\n" + "\n".join(files))
    if constraints and str(constraints).strip():
        blocks.append("[Constraints]\n" + str(constraints).strip())
    if mode == "explore":
        blocks.append(
            "Mode: explore (read-only). Use read/glob/grep only. Do not write, edit, or run shell."
        )
    blocks.append(
        "Do only the assigned task. Call finish_worker(result, files_changed, status) when done; report this task only."
    )
    return [{"role": "user", "content": "\n\n".join(blocks)}]


def validate_worker_tasks(
    tasks: list[str] | list[WorkerSpec],
    main_history: list[dict] | None,
    running_tasks: list[str] | None = None,
) -> str | None:
    """通过返回 None；否则返回拒绝原因。"""
    cleaned: list[str] = []
    for t in tasks or []:
        if isinstance(t, WorkerSpec):
            text = t.task
        else:
            text = str(t).strip()
        if text:
            cleaned.append(text)
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
    specs = [t for t in (tasks or []) if isinstance(t, WorkerSpec)]
    if len(specs) >= 2:
        file_sets = [set(s.files) for s in specs if s.files]
        if len(file_sets) >= 2:
            for i, a in enumerate(file_sets):
                for b in file_sets[i + 1 :]:
                    if a & b:
                        return "[拒绝] 本批工人声明的 files 有重叠，会抢同一批文件。请拆开或由主 agent 自己做。"
    return None


def format_batch_report(workers: list[dict], instruction: str | None = None) -> str:
    """给主 agent 看的工人批次报告（JSON 文本，作为 spawn_* 的 tool 结果）。"""
    payload = {
        "type": "worker_batch",
        "workers": workers,
        "instruction": instruction
        or (
            "这些是你派出的工人报告，不是用户发言。按 done_when 验收后合并；"
            "未达标则自己补做或重派（仍须满足派生条件）。不要把工人原文当最终答复。"
            "工人未完成前不要调用 finish_task。"
        ),
    }
    return json.dumps(payload, ensure_ascii=False)
