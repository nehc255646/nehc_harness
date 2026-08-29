"""OpenAI 兼容流：思考/正文分通道 + <think> 切分。"""

from __future__ import annotations

from dataclasses import dataclass, field


def _as_text(val) -> str:
    return val if isinstance(val, str) else ""


def _find_ci(hay: str, needle: str) -> int:
    return hay.lower().find(needle.lower())


def _partial_suffix(buf: str, token: str) -> int:
    """buf 末尾与 token 前缀重叠的长度（忽略大小写）。"""
    lower_buf = buf.lower()
    lower_tok = token.lower()
    max_n = min(len(buf), len(token) - 1)
    for n in range(max_n, 0, -1):
        if lower_tok.startswith(lower_buf[-n:]):
            return n
    return 0


class ThinkTagSplitter:
    """跨 chunk 切分 <think>…</think>（大小写不敏感）。"""

    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self) -> None:
        self.in_think = False
        self.buf = ""

    def feed(self, text: str) -> tuple[str, str]:
        if not text:
            return "", ""
        self.buf += text
        thinking: list[str] = []
        content: list[str] = []
        while self.buf:
            if self.in_think:
                idx = _find_ci(self.buf, self.CLOSE)
                if idx < 0:
                    keep = _partial_suffix(self.buf, self.CLOSE)
                    emit = self.buf[:-keep] if keep else self.buf
                    if emit:
                        thinking.append(emit)
                    self.buf = self.buf[-keep:] if keep else ""
                    break
                thinking.append(self.buf[:idx])
                self.buf = self.buf[idx + len(self.CLOSE) :]
                self.in_think = False
            else:
                idx = _find_ci(self.buf, self.OPEN)
                if idx < 0:
                    keep = _partial_suffix(self.buf, self.OPEN)
                    emit = self.buf[:-keep] if keep else self.buf
                    if emit:
                        content.append(emit)
                    self.buf = self.buf[-keep:] if keep else ""
                    break
                content.append(self.buf[:idx])
                self.buf = self.buf[idx + len(self.OPEN) :]
                self.in_think = True
        return "".join(thinking), "".join(content)

    def flush(self) -> tuple[str, str]:
        leftover = self.buf
        self.buf = ""
        if self.in_think:
            return leftover, ""
        return "", leftover


@dataclass
class StreamDelta:
    thinking: str = ""
    content: str = ""
    tool_call_chunks: list = field(default_factory=list)


def _tool_call_chunks_from_delta(tool_calls) -> list[dict]:
    out: list[dict] = []
    for tc in tool_calls or []:
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
            if not isinstance(fn, dict):
                fn = {}
            out.append(
                {
                    "index": tc.get("index"),
                    "id": tc.get("id") or "",
                    "name": fn.get("name") or tc.get("name") or "",
                    "args": fn.get("arguments") if fn.get("arguments") is not None else tc.get("args") or "",
                }
            )
            continue
        fn = getattr(tc, "function", None)
        name = ""
        args = ""
        if fn is not None:
            if isinstance(fn, dict):
                name = fn.get("name") or ""
                args = fn.get("arguments") or ""
            else:
                name = getattr(fn, "name", None) or ""
                args = getattr(fn, "arguments", None) or ""
        out.append(
            {
                "index": getattr(tc, "index", None),
                "id": getattr(tc, "id", None) or "",
                "name": name or getattr(tc, "name", None) or "",
                "args": args if args is not None else "",
            }
        )
    return out


def _delta_dict(chunk) -> dict | None:
    choices = getattr(chunk, "choices", None)
    if choices:
        delta = getattr(choices[0], "delta", None)
        if delta is not None:
            extra = getattr(delta, "model_extra", None) or {}
            if not isinstance(extra, dict):
                extra = {}
            return {
                "content": getattr(delta, "content", None),
                "reasoning_content": getattr(delta, "reasoning_content", None) or extra.get("reasoning_content"),
                "reasoning": getattr(delta, "reasoning", None) or extra.get("reasoning"),
                "tool_calls": getattr(delta, "tool_calls", None),
            }
    if isinstance(chunk, dict) and chunk.get("choices"):
        delta = (chunk["choices"][0] or {}).get("delta") or {}
        if isinstance(delta, dict):
            return delta
    return None


def _blocks_to_channels(content) -> tuple[str, str]:
    if isinstance(content, str) or content is None:
        return "", _as_text(content)
    if not isinstance(content, list):
        return "", ""
    thinking: list[str] = []
    texts: list[str] = []
    for b in content:
        if not isinstance(b, dict):
            continue
        btype = b.get("type")
        if btype in ("reasoning", "thinking"):
            thinking.append(_as_text(b.get("reasoning") or b.get("thinking") or b.get("text")))
        elif btype == "text":
            texts.append(_as_text(b.get("text")))
    return "".join(thinking), "".join(texts)


def extract_chunk_channels(chunk) -> StreamDelta:
    """从 OpenAI SDK / dict / LangChain chunk 抽出思考、正文、tool_call 碎片。"""
    d = _delta_dict(chunk)
    if d is not None:
        thinking = _as_text(d.get("reasoning_content")) or _as_text(d.get("reasoning"))
        return StreamDelta(
            thinking=thinking,
            content=_as_text(d.get("content")),
            tool_call_chunks=_tool_call_chunks_from_delta(d.get("tool_calls")),
        )

    additional = getattr(chunk, "additional_kwargs", None) or {}
    if not isinstance(additional, dict) and isinstance(chunk, dict):
        additional = chunk.get("additional_kwargs") or {}
    if not isinstance(additional, dict):
        additional = {}
    thinking = _as_text(additional.get("reasoning_content")) or _as_text(additional.get("reasoning"))
    raw_content = getattr(chunk, "content", None)
    if raw_content is None and isinstance(chunk, dict):
        raw_content = chunk.get("content")
    block_th, text = _blocks_to_channels(raw_content)
    thinking = thinking + block_th
    tcs = getattr(chunk, "tool_call_chunks", None)
    if tcs is None and isinstance(chunk, dict):
        tcs = chunk.get("tool_call_chunks")
    return StreamDelta(thinking=thinking, content=text, tool_call_chunks=list(tcs or []))


async def iter_channels(executor, messages: list[dict], tools: list | None = None):
    """把 executor 流拆成 (thinking, content, tool_call_chunks) 三元组。"""
    splitter = ThinkTagSplitter()
    async for chunk in executor.astream_with_retry(messages, tools):
        part = extract_chunk_channels(chunk)
        tag_th, tag_ct = splitter.feed(part.content)
        thinking = (part.thinking or "") + tag_th
        if thinking or tag_ct or part.tool_call_chunks:
            yield thinking, tag_ct, part.tool_call_chunks
    th_f, ct_f = splitter.flush()
    if th_f or ct_f:
        yield th_f, ct_f, []


def thinking_extra_body(request_thinking: bool, reasoning_effort: str | None = None) -> dict | None:
    if not request_thinking:
        return None
    extra: dict = {"enable_thinking": True}
    effort = (reasoning_effort or "").strip()
    if effort:
        extra["reasoning"] = {"effort": effort}
    return extra
