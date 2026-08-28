"""context 单元测试 — 摘要阈值/注入顺序/大结果截断 (PLAN §2.3)"""

from app.agent.context import (
    build_messages,
    estimate_tokens,
    should_summarize,
    truncate_tool_result,
    window_slice,
)


def test_build_messages_order():
    msgs = build_messages("sys", "summary", [{"role": "user", "content": "u"}], [{"role": "user", "content": "p"}])
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "sys"
    assert msgs[1]["role"] == "system"
    assert msgs[1]["content"] == "[摘要]\nsummary"
    assert msgs[2]["content"] == "u"
    assert msgs[3]["content"] == "p"


def test_build_messages_without_summary():
    msgs = build_messages("sys", None, [{"role": "user", "content": "u"}], [])
    assert len(msgs) == 2


def test_should_summarize():
    assert should_summarize(100, 200, 0.65) is False
    assert should_summarize(150, 200, 0.65) is True


def test_truncate_tool_result_short():
    assert truncate_tool_result("hello", 8192) == "hello"


def test_truncate_tool_result_long():
    content = "unique-token-fragment " * 4000
    result = truncate_tool_result(content, 64)
    assert "截断" in result
    assert result.startswith("unique-token-fragment")
    assert len(result) < len(content)


def test_estimate_tokens_and_window():
    msgs = [{"role": "user", "content": "hello"}] * 10
    assert estimate_tokens(msgs) > 0
    slid, window = window_slice(msgs, 2)
    assert len(window) == 4
    assert len(slid) == 6


def test_window_slice_snaps_tool_group_boundary():
    """窗口起点落在 tool 结果上时，向前扩展到所属 assistant(tool_calls)，保持配对完整"""
    history = [{"role": "user", "content": f"m{i}"} for i in range(5)]
    # 一组 tool 对话：assistant(tool_calls) + 3 条 tool 结果
    tool_group = [
        {"role": "assistant", "content": "", "tool_calls": [{"name": "shell", "args": {}, "id": "c1"}]},
        {"role": "tool", "content": "out1", "tool_call_id": "c1", "name": "shell"},
        {"role": "tool", "content": "out2", "tool_call_id": "c1", "name": "shell"},
        {"role": "tool", "content": "out3", "tool_call_id": "c1", "name": "shell"},
    ]
    history.extend(tool_group)
    history.append({"role": "user", "content": "final"})
    _slid, window = window_slice(history, 2)  # keep=4，硬切起点=6 恰落在 tool 上
    # 吸附后窗口首条不得是 tool，应为所属 assistant(tool_calls)
    assert window[0]["role"] != "tool"
    assert window[0].get("tool_calls"), "窗口应包含完整 assistant(tool_calls) 组"
    assert len(window) == 5