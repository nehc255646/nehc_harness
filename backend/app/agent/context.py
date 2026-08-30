"""上下文管理 — 消息构造 + 流式 tool_calls 累积解析 + 大结果截断 + 摘要触发"""

import hashlib
import json
import logging
import re
import uuid

logger = logging.getLogger("neharness.context")

try:
    import tiktoken

    _enc = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover
    _enc = None


def build_messages(system: str, summary: str | None, window_messages: list, pending: list) -> list:
    """注入顺序：system + 摘要 + 窗口消息 + 本轮"""
    messages = [{"role": "system", "content": system}]
    if summary:
        messages.append({"role": "system", "content": f"[Summary]\n{summary}"})
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


def unmatched_tool_results(history: list[dict]) -> list[dict]:
    """assistant.tool_calls 缺少对应 tool 行时补合成错误，避免重启后发给模型 400。"""
    seen: set[str] = set()
    for m in history:
        if m.get("role") == "tool" and m.get("tool_call_id"):
            seen.add(str(m["tool_call_id"]))
    extra: list[dict] = []
    for m in history:
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            cid = str(tc.get("id") or "")
            if not cid or cid in seen:
                continue
            extra.append(
                {
                    "role": "tool",
                    "content": "[中断] 工具结果未落库（进程停止或崩溃）",
                    "tool_call_id": cid,
                    "name": tc.get("name") or "",
                }
            )
            seen.add(cid)
    return extra


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


def slid_fingerprint(slid: list[dict]) -> str:
    """滑出消息指纹，用于摘要缓存去重。"""
    raw = json.dumps(slid, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def next_summary_cache(
    prev: dict | None,
    text: str | None,
    slid_count: int,
    fingerprint: str,
    pending_slid: list | None = None,
) -> dict:
    """摘要缓存下一版：成功则升版本并清空 pending；失败则把滑出消息留在 pending_slid。"""
    prev = prev or {}
    if text:
        return {
            "text": text,
            "version": int(prev.get("version") or 0) + 1,
            "covered_count": int(prev.get("covered_count") or 0) + slid_count,
            "last_slid_hash": fingerprint,
            "pending_slid": [],
        }
    return {
        "text": prev.get("text"),
        "version": int(prev.get("version") or 0),
        "covered_count": int(prev.get("covered_count") or 0),
        "last_slid_hash": prev.get("last_slid_hash"),
        "pending_slid": list(pending_slid or []),
    }


# ---------- 流式 tool_calls 累积解析（主 agent 与子 agent 共用） ----------


def tc_field(tc, key: str):
    """chunk 兼容取值：langchain TypedDict (dict) 与对象属性两种形态"""
    if isinstance(tc, dict):
        return tc.get(key)
    return getattr(tc, key, None)


def new_tool_call_acc(index: int) -> dict:
    return {"name": "", "args": "", "id": "", "index": index}


def _as_index(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _index_for_tool_call(tc, acc: dict[int, dict]) -> int:
    """优先用供应商给的 index；否则按 id 归并；禁止把缺 index 的完整块再开一槽。"""
    idx = _as_index(tc_field(tc, "index"))
    if idx is not None:
        return idx
    tid = tc_field(tc, "id")
    if tid:
        for k, v in acc.items():
            if v.get("id") == tid:
                return k
    if len(acc) == 1:
        only_k = next(iter(acc.keys()))
        only_id = acc[only_k].get("id") or ""
        if not tid or not only_id or only_id == tid:
            return only_k
    return max(acc.keys(), default=-1) + 1


def _merge_tool_args(entry: dict, args) -> None:
    """碎片拼接与已解析 dict 合并；空值不覆盖已累积内容。"""
    if args is None or args == "" or args == {}:
        return
    cur = entry.get("args")
    if isinstance(args, dict):
        if isinstance(cur, dict) and cur:
            cur.update(args)
        else:
            entry["args"] = dict(args)
        return
    if isinstance(args, str):
        if isinstance(cur, dict) and cur:
            return
        if isinstance(cur, str):
            entry["args"] = cur + args
        else:
            entry["args"] = args


def accumulate_tool_calls(acc: dict[int, dict], chunk) -> None:
    """从流式 chunk 累积 tool_calls。

    args 兼容两种形态：字符串碎片逐段拼接；已解析 dict（部分供应商/完整块）合并进同一 index。
    """
    fragments = tc_field(chunk, "tool_call_chunks")
    if not isinstance(fragments, list):
        fragments = []
    for tc in fragments:
        idx = _index_for_tool_call(tc, acc)
        if idx not in acc:
            acc[idx] = new_tool_call_acc(idx)
        entry = acc[idx]
        name = tc_field(tc, "name")
        if name:
            entry["name"] = name
        tid = tc_field(tc, "id")
        if tid:
            entry["id"] = tid
        _merge_tool_args(entry, tc_field(tc, "args"))

    complete = tc_field(chunk, "tool_calls")
    if not isinstance(complete, list) or not complete or complete is fragments:
        return
    for tc in complete:
        idx = _index_for_tool_call(tc, acc)
        if idx not in acc:
            acc[idx] = new_tool_call_acc(idx)
        entry = acc[idx]
        name = tc_field(tc, "name")
        if name:
            entry["name"] = name
        tid = tc_field(tc, "id")
        if tid:
            entry["id"] = tid
        _merge_tool_args(entry, tc_field(tc, "args"))


_CMD_IN_JSON_RE = re.compile(r'"(?:command|cmd)"\s*:\s*"((?:\\.|[^"\\])*)"', re.DOTALL)


def normalize_tool_args(raw_args) -> dict:
    """把流式/畸形 args 收成 dict；能认出 command 就不要落到 __raw。"""
    if raw_args is None or raw_args == "":
        return {}
    if isinstance(raw_args, dict):
        if list(raw_args.keys()) == ["__raw"]:
            return normalize_tool_args(raw_args.get("__raw"))
        return raw_args
    if isinstance(raw_args, (list, tuple)):
        parts = [str(x) for x in raw_args if x is not None and str(x).strip()]
        return {"command": " ".join(parts)} if parts else {}
    if not isinstance(raw_args, str):
        return {}
    s = raw_args.strip()
    if not s:
        return {}
    cur = s
    for _ in range(2):
        try:
            parsed = json.loads(cur)
        except Exception:
            break
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, str):
            cur = parsed.strip()
            continue
        break
    m = _CMD_IN_JSON_RE.search(s)
    if m:
        try:
            return {"command": json.loads(f'"{m.group(1)}"')}
        except Exception:
            return {"command": m.group(1)}
    if s[0] not in "{[":
        return {"command": s}
    return {"__raw": raw_args}


def shell_command(args) -> str:
    """取出可执行的 command 字符串；没有则空串。"""
    data = normalize_tool_args(args)
    if not isinstance(data, dict):
        return ""
    cmd = data.get("command")
    if cmd is None:
        cmd = data.get("cmd")
    if isinstance(cmd, (list, tuple)):
        cmd = " ".join(str(x) for x in cmd)
    if cmd is None:
        return ""
    return str(cmd).strip()


def parse_tool_calls(acc: dict[int, dict]) -> list[dict]:
    """将累积结果解析为 [{name, args(dict), id}]，args 已是 dict 则直接使用"""
    tool_calls: list[dict] = []
    for idx in sorted(acc.keys()):
        raw = acc[idx]
        name = raw.get("name", "")
        if not name:
            continue
        args = normalize_tool_args(raw.get("args", ""))
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
