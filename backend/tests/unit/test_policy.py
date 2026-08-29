"""policy 单元测试 — 用户门四路分流 (PLAN §2.2)"""

from app.permissions.policy import check_policy


def test_blacklist_blocked():
    decision, _, need = check_policy("shell", {"command": "rm -rf /"}, [])
    assert decision == "blocked"
    assert need is False


def test_config_allow_shell():
    decision, _, need = check_policy("shell", {"command": "git status"}, [])
    assert decision == "config_allow"
    assert need is False


def test_config_allow_tool():
    decision, _, need = check_policy("read", {"path": "a.txt"}, [])
    assert decision == "config_allow"
    assert need is False


def test_readonly_allow():
    decision, _, need = check_policy("glob", {"pattern": "**/*"}, [])
    assert decision == "config_allow"
    assert need is False


def test_need_approval():
    decision, reason, need = check_policy("shell", {"command": "echo hello"}, [])
    assert decision == "need_approval"
    assert need is True
    assert "shell" in reason


def test_session_allow():
    rules = [{"kind": "tool", "pattern": "write"}]
    decision, _, need = check_policy("write", {"path": "a.txt", "content": "x"}, rules)
    assert decision == "session_allow"
    assert need is False


def test_plan_mode_blocks_mutating_even_if_session_allow():
    rules = [{"kind": "tool", "pattern": "write"}]
    decision, reason, need = check_policy("write", {"path": "a.txt", "content": "x"}, rules, work_mode="plan")
    assert decision == "blocked"
    assert need is False
    assert "plan" in reason


def test_plan_mode_allows_read():
    decision, _, need = check_policy("read", {"path": "a.txt"}, [], work_mode="plan")
    assert decision == "config_allow"
    assert need is False


def test_plan_mode_blocks_shell():
    decision, _, need = check_policy("shell", {"command": "git status"}, [], work_mode="plan")
    assert decision == "blocked"
    assert need is False
