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
# rm 的递归+强制组合由 _rm_rf_hit 按 token 判定（覆盖 -rf/-r -f/--recursive --force 等写法），不在此列
_DEFAULT_BLACKLIST_PATTERNS: list[tuple[str, str]] = [
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

_RM_FLAG_RECURSIVE = ("r", "R", "recursive")
_RM_FLAG_FORCE = ("f", "force")


def _rm_rf_hit(tokens: list[str]) -> bool:
    """rm 同时带递归与强制旗标即命中（兼容 sudo 前缀与组合短旗标如 -rf/-fr）"""
    start = 1 if tokens and tokens[0] == "sudo" else 0
    if len(tokens) <= start or tokens[start] != "rm":
        return False
    recursive = force = False
    for t in tokens[start + 1 :]:
        if t == "--":
            break  # -- 之后的都是文件名
        if t.startswith("--"):
            name = t[2:].split("=", 1)[0]
            if name in _RM_FLAG_RECURSIVE:
                recursive = True
            elif name in _RM_FLAG_FORCE:
                force = True
        elif re.fullmatch(r"-[a-zA-Z]+", t):
            letters = set(t[1:])
            if letters & {"r", "R"}:
                recursive = True
            if "f" in letters:
                force = True
    return recursive and force


def is_blacklisted(command: str) -> tuple[bool, str | None]:
    """检查是否命中黑名单，返回 (是否命中, 命中项描述)"""
    if not settings.blacklist_enabled:
        return False, None
    for sub in _split_shell_commands(command):
        sub = sub.strip()
        if not sub:
            continue
        if _rm_rf_hit(sub.split()):
            return True, "rm -rf"
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
