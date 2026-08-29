"""思考通道：字段抽取与 <think> 切分。"""

from types import SimpleNamespace

from app.agent.stream import ThinkTagSplitter, extract_chunk_channels, thinking_extra_body


def test_reasoning_content_only():
    chunk = SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    reasoning_content="先看目录",
                    reasoning=None,
                    tool_calls=None,
                    model_extra={},
                )
            )
        ]
    )
    d = extract_chunk_channels(chunk)
    assert d.thinking == "先看目录"
    assert d.content == ""


def test_reasoning_field_vllm():
    chunk = SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    reasoning_content=None,
                    reasoning="step 1",
                    tool_calls=None,
                    model_extra={},
                )
            )
        ]
    )
    d = extract_chunk_channels(chunk)
    assert d.thinking == "step 1"


def test_dict_delta_and_content():
    chunk = {
        "choices": [
            {
                "delta": {
                    "reasoning_content": "想",
                    "content": "答",
                    "tool_calls": [
                        {"index": 0, "id": "c1", "function": {"name": "shell", "arguments": "{\"c"}}
                    ],
                }
            }
        ]
    }
    d = extract_chunk_channels(chunk)
    assert d.thinking == "想"
    assert d.content == "答"
    assert d.tool_call_chunks[0]["name"] == "shell"
    assert d.tool_call_chunks[0]["args"] == "{\"c"


def test_langchain_additional_kwargs():
    chunk = SimpleNamespace(
        content="hello",
        additional_kwargs={"reasoning_content": "hmm"},
        tool_call_chunks=[],
    )
    d = extract_chunk_channels(chunk)
    assert d.thinking == "hmm"
    assert d.content == "hello"


def test_think_tags_split_across_chunks():
    s = ThinkTagSplitter()
    th, ct = s.feed("pre <thi")
    assert th == ""
    assert ct == "pre "
    th, ct = s.feed("nk>secret")
    assert "secret" in th
    assert ct == ""
    th, ct = s.feed(" more</th")
    assert " more" in th
    th, ct = s.feed("ink>out")
    assert ct == "out"
    th, ct = s.flush()
    assert th == ""
    assert ct == ""


def test_think_tags_same_chunk():
    s = ThinkTagSplitter()
    th, ct = s.feed("A<think>B</think>C")
    assert th == "B"
    assert ct == "AC"


async def test_iter_channels_reasoning_then_content():
    class Fake:
        async def astream_with_retry(self, messages, tools=None):
            yield {
                "choices": [{"delta": {"reasoning_content": "想一下", "content": None}}],
            }
            yield {
                "choices": [{"delta": {"content": "<think>内部</think>答案"}}],
            }

    from app.agent.stream import iter_channels

    parts = []
    async for item in iter_channels(Fake(), []):
        parts.append(item)
    thinking = "".join(p[0] for p in parts)
    content = "".join(p[1] for p in parts)
    assert "想一下" in thinking
    assert "内部" in thinking
    assert content == "答案"


def test_thinking_extra_body():
    assert thinking_extra_body(False) is None
    assert thinking_extra_body(True) == {"enable_thinking": True}
    assert thinking_extra_body(True, "high") == {"enable_thinking": True, "reasoning": {"effort": "high"}}
