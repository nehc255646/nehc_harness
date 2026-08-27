"""上下文管理 — 摘要 + 滑动窗口 + 大结果截断 (M3/M4 实现)"""


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


def truncate_tool_result(content: str, max_tokens: int = 8192) -> str:
    # TODO: tiktoken 精确截断，保留头尾 + 省略标记
    # 占位按字符近似
    limit = max_tokens * 4
    if len(content) <= limit:
        return content
    head = limit // 2
    tail = limit // 2
    return content[:head] + f"\n\n...[截断 {len(content)-limit} chars]...\n\n" + content[-tail:]
