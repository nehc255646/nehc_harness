"""rules 单元测试 — 黑名单拆分/前缀匹配/会话规则 (PLAN §8)"""

from app.permissions.rules import (
    extract_shell_prefix,
    is_blacklisted,
    is_session_shell_allowed,
    is_session_tool_allowed,
    is_shell_prefix_allowed,
    is_tool_allowed,
)


def test_blacklist_basic():
    assert is_blacklisted("rm -rf /") == (True, "rm -rf")
    assert is_blacklisted("mkfs.ext4 /dev/sda1") == (True, "mkfs")
    assert is_blacklisted("ls -la") == (False, None)


def test_blacklist_split_by_separators():
    # 按 ; && || | 拆分后逐段匹配
    assert is_blacklisted("echo hi && rm -rf /tmp/x") == (True, "rm -rf")
    assert is_blacklisted("ls -la; sudo rm -rf /") == (True, "rm -rf")
    assert is_blacklisted("cat a.txt | shred") == (True, "shred")
    assert is_blacklisted("echo hi || echo bye") == (False, None)


def test_shell_prefix_extract():
    assert extract_shell_prefix("git push origin main") == "git push"
    assert extract_shell_prefix("  ls   -la  ") == "ls -la"
    assert extract_shell_prefix("") == ""


def test_config_allow():
    # allow_rules.yaml 中已配置
    assert is_shell_prefix_allowed("git status") is True
    assert is_shell_prefix_allowed("git status --short") is True
    assert is_shell_prefix_allowed("git push") is False  # 未配置
    assert is_tool_allowed("read") is True
    assert is_tool_allowed("write") is False


def test_session_allow():
    rules = [{"kind": "shell_prefix", "pattern": "git push"}]
    assert is_session_shell_allowed("git push origin main", rules) is True
    assert is_session_shell_allowed("git reset --hard", rules) is False
    assert is_session_tool_allowed("write", [{"kind": "tool", "pattern": "write"}]) is True
    assert is_session_tool_allowed("write", []) is False