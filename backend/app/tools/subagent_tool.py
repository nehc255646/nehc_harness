"""finish_subagent — 交互型收敛；呼出由用户从顶栏发起"""

from langchain_core.tools import tool


@tool
def finish_subagent(summary: str) -> str:
    """交互型收敛：结束对话并将摘要回投主 agent。参数: summary"""
    return f"[占位] finish_subagent summary={summary[:100]}"
