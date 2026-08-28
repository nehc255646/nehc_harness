"""上下文管理 — 消息构造 + 流式 tool_calls 累积解析 + 大结果截断 + 摘要触发"""

import json
import logging
import uuid

logger = logging.getLogger("harness.context")

try:
    import tiktoken

    _enc = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover
    _enc = None


def build_messages(system: str, summary: str | None, window_messages: list, pending: list) -> list:
    """注入顺序：system + 摘要 + 窗口消息 + 本轮 — PLAN.md §2.3"""
    messages = [{"role": "system", "content": system}]
    if summary:
        messages.append({"role": "system", "content": f"[摘要]\n{summary}"})
    messages.extend(window_messages)
    messages.extend(pending)
    return messages


def should_summarize(total_tokens: int, context_window: int, ratio: float = 0.65) -> bool:
    return total_tokens >= ratio * context_window


def estimate_tokens(messages: list[dict], summary: str | None = None) -> int:
    parts: list[str] = []
    if summary:
        parts.append(summary)
    for m in messages:
        parts.append(str(m.get("content", "") or ""))
        if m.get("tool_calls"):
            parts.append(json.dumps(m["tool_calls"], ensure_ascii=False))
    text = "\n".join(parts)
    if _enc is not None:
        try:
            return len(_enc.encode(text))
        except Exception:
            logger.debug("tiktoken encode failed, fallback to chars/4")
    return max(1, len(text) // 4)


def window_slice(history: list[dict], window_n: int) -> tuple[list[dict], list[dict]]:
    """按 turn 近似：保留最近 window_n*2 条。返回 (slid_out, window)。

    窗口起点吸附到 tool 组边界：起点若为 tool 结果，则向前扩展至所属的
    assistant(tool_calls) 行，避免窗口切开 tool_calls/tool 配对导致模型 API 400。
    """
    keep = max(window_n * 2, 1)
    if len(history) <= keep:
        return [], list(history)
    start = len(history) - keep
    while start > 0 and history[start].get("role") == "tool":
        start -= 1
    return history[:start], history[start:]


# ---------- 流式 tool_calls 累积解析（主 agent 与子 agent 共用） ----------


def tc_field(tc, key: str):
    """chunk 兼容取值：langchain TypedDict (dict) 与对象属性两种形态"""
    if isinstance(tc, dict):
        return tc.get(key)
    return getattr(tc, key, None)


def new_tool_call_acc(index: int) -> dict:
    return {"name": "", "args": "", "id": "", "index": index}


def accumulate_tool_calls(acc: dict[int, dict], chunk) -> None:
    """从流式 chunk 累积 tool_calls。

    args 兼容两种形态：字符串碎片逐段拼接；已解析 dict（部分供应商/完整块）整体覆盖。
    """
    chunks = tc_field(chunk, "tool_call_chunks") or tc_field(chunk, "tool_calls") or []
    for tc in chunks:
        idx = tc_field(tc, "index") or 0
        if idx not in acc:
            acc[idx] = new_tool_call_acc(idx)
        entry = acc[idx]
        name = tc_field(tc, "name")
        if name:
            entry["name"] = name
        tid = tc_field(tc, "id")
        if tid:
            entry["id"] = tid
        args = tc_field(tc, "args")
        if args:
            if isinstance(args, str):
                entry["args"] += args
            else:
                entry["args"] = args

    # 完整 tool_calls（已解析 dict）整体覆盖，保证最终态正确
    complete = tc_field(chunk, "tool_calls")
    if isinstance(complete, list) and complete and complete is not chunks:
        for tc in complete:
            idx = tc_field(tc, "index")
            if idx is None:
                idx = max(acc.keys(), default=-1) + 1
            acc[idx] = {
                "name": tc_field(tc, "name") or "",
                "args": tc_field(tc, "args") or "",
                "id": tc_field(tc, "id") or str(uuid.uuid4()),
                "index": idx,
            }


def parse_tool_calls(acc: dict[int, dict]) -> list[dict]:
    """将累积结果解析为 [{name, args(dict), id}]，args 已是 dict 则直接使用"""
    tool_calls: list[dict] = []
    for idx in sorted(acc.keys()):
        raw = acc[idx]
        name = raw.get("name", "")
        if not name:
            continue
        raw_args = raw.get("args", "")
        if isinstance(raw_args, dict):
            args = raw_args
        else:
            try:
                args = json.loads(raw_args) if raw_args else {}
                if isinstance(args, str):
                    args = json.loads(args)
            except Exception:
                args = {"__raw": raw_args}
        tool_calls.append({"name": name, "args": args, "id": raw.get("id") or str(uuid.uuid4())})
    return tool_calls


def truncate_tool_result(content: str, max_tokens: int = 8192) -> str:
    if _enc is not None:
        try:
            toks = _enc.encode(content)
            if len(toks) <= max_tokens:
                return content
            half = max(1, max_tokens // 2)
            head = _enc.decode(toks[:half])
            tail = _enc.decode(toks[-half:])
            return head + f"\n\n...[截断 {len(toks) - max_tokens} tokens]...\n\n" + tail
        except Exception:
            logger.debug("tiktoken truncate failed, fallback to chars")
    limit = max_tokens * 4
    if len(content) <= limit:
        return content
    head = limit // 2
    tail = limit // 2
    return content[:head] + f"\n\n...[截断 {len(content) - limit} chars]...\n\n" + content[-tail:]
