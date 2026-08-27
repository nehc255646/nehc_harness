"""规则解析与前缀匹配 — 对应 PLAN.md §2.2"""

import re
from pathlib import Path

import yaml

from app.core.config import settings


def _load_yaml() -> dict:
    path = Path(settings.allow_rules_file)
    if not path.is_absolute():
        # 相对于项目根 (backend/..)
        path = (Path(__file__).resolve().parents[3] / settings.allow_rules_file).resolve()
        # 兼容启动目录为 backend 时
        if not path.exists():
            path = Path(settings.allow_rules_file).resolve()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            return data
    except Exception:
        return {}


def get_allow_shell_prefixes() -> list[str]:
    return _load_yaml().get("allow_shell", [])


def get_allow_tools() -> list[str]:
    return _load_yaml().get("allow_tools", [])


def extract_shell_prefix(command: str, n: int = 2) -> str:
    """取命令前 n 个 token 作为前缀，用于同类判定 (PLAN 定稿: 固定 2)"""
    # 去除前后空白，取第一段子命令 (按 ; && || | 分割取首段仅用于前缀提取？实际匹配时对每段都检查)
    # 前缀提取直接对原始 command 的 token 化
    tokens = command.strip().split()
    if not tokens:
        return ""
    return " ".join(tokens[:n])


def is_shell_prefix_allowed(command: str) -> bool:
    """检查是否命中 allow_rules.yaml 的 allow_shell 前缀"""
    prefixes = get_allow_shell_prefixes()
    if not prefixes:
        return False
    # 按 bash 语义拆分子命令后逐段检查 (与黑名单一致)
    for sub in _split_shell_commands(command):
        sub = sub.strip()
        if not sub:
            continue
        for p in prefixes:
            if sub == p or sub.startswith(p + " "):
                return True
    return False


def is_tool_allowed(tool_name: str) -> bool:
    return tool_name in get_allow_tools()


# ---------- Shell 拆分 ----------

_SPLIT_RE = re.compile(r"\s*(?:;|&&|\|\||\|)\s*")


def _split_shell_commands(command: str) -> list[str]:
    """按 ; && || | 拆分子命令，用于黑名单逐段匹配"""
    # 简易拆分，足以覆盖个人使用场景
    return _SPLIT_RE.split(command)


# ---------- 黑名单 ----------

# 默认集 — 常见破坏性命令 (可配置关闭)
_DEFAULT_BLACKLIST_PATTERNS: list[tuple[str, str]] = [
    (r"\brm\s+.*-rf\b", "rm -rf"),
    (r"\brm\s+-rf\b", "rm -rf"),
    (r"\bmkfs\b", "mkfs"),
    (r"\bdd\b.*\bof=/dev/", "dd of=/dev"),
    (r"\bsudo\s+rm\b", "sudo rm"),
    (r"\bchmod\s+-R\s+777\s+/", "chmod -R 777 /"),
    (r"\bshred\b", "shred"),
    (r"\bwipefs\b", "wipefs"),
    (r":\(\)\s*\{\s*:\|\:&\s*\}\s*;\s*:", "fork bomb"),
    (r"\bshutdown\b", "shutdown"),
    (r"\breboot\b", "reboot"),
    (r"\bhalt\b", "halt"),
    (r"\bpoweroff\b", "poweroff"),
]


def is_blacklisted(command: str) -> tuple[bool, str | None]:
    """检查是否命中黑名单，返回 (是否命中, 命中项描述)"""
    if not settings.blacklist_enabled:
        return False, None
    for sub in _split_shell_commands(command):
        sub = sub.strip()
        if not sub:
            continue
        for pattern, desc in _DEFAULT_BLACKLIST_PATTERNS:
            if re.search(pattern, sub):
                return True, desc
    return False, None


# ---------- 会话级规则 ----------

def is_session_shell_allowed(command: str, session_rules: list[dict]) -> bool:
    """检查是否命中会话放行规则 (kind=shell_prefix)"""
    for rule in session_rules:
        if rule.get("kind") != "shell_prefix":
            continue
        pat = rule.get("pattern", "")
        for sub in _split_shell_commands(command):
            sub = sub.strip()
            if sub == pat or sub.startswith(pat + " "):
                return True
    return False


def is_session_tool_allowed(tool_name: str, session_rules: list[dict]) -> bool:
    for rule in session_rules:
        if rule.get("kind") == "tool" and rule.get("pattern") == tool_name:
            return True
    return False
