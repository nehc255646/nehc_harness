"""工作型派生：拒绝整单转包、工人上下文隔离"""

from app.agent.loop import AgentLoop
from app.agent.spawn_policy import (
    WorkerSpec,
    history_has_readonly_explore,
    last_user_goal,
    parse_worker_specs,
    path_in_scope,
    task_restates_goal,
    tasks_redundant,
    validate_worker_tasks,
    worker_brief_messages,
)
from app.agent.subagent import SubAgentLoop


def test_last_user_goal_skips_synthetic():
    hist = [
        {"role": "user", "content": "实现登录"},
        {"role": "assistant", "content": "好"},
        {"role": "user", "content": "[工作型批量完成] batch=1"},
    ]
    assert last_user_goal(hist) == "实现登录"


def test_task_restates_goal():
    assert task_restates_goal("实现登录", "实现登录") is True
    assert task_restates_goal("实现登录并写完所有测试文档", "实现登录") is True
    assert task_restates_goal("改 auth.py 的校验函数", "实现登录和支付") is False
    assert task_restates_goal("实现登录", "实现登录和支付") is False


def test_tasks_redundant():
    assert tasks_redundant("列出 workspace 文件", "列出 workspace 文件") is True
    assert tasks_redundant("读取 hello.txt", "列出顶层目录") is False


def test_validate_rejects_clone_of_user_goal():
    hist = [{"role": "user", "content": "帮我把整个项目重构一遍"}]
    msg = validate_worker_tasks(["帮我把整个项目重构一遍"], hist)
    assert msg and "重复" in msg


def test_validate_rejects_sibling_clones():
    hist = [{"role": "user", "content": "实现登录和支付"}]
    msg = validate_worker_tasks(["改登录模块", "改登录模块"], hist)
    assert msg and "互相重复" in msg


def test_validate_rejects_running_duplicate():
    hist = [{"role": "user", "content": "实现登录和支付"}]
    msg = validate_worker_tasks(["改支付回调"], hist, running_tasks=["改支付回调"])
    assert msg and "已有工人" in msg


def test_validate_allows_disjoint_slices():
    hist = [{"role": "user", "content": "实现登录和支付"}]
    assert validate_worker_tasks(["改 auth.py 登录校验", "改 pay.py 回调签名"], hist) is None


def test_worker_brief_does_not_copy_main_history():
    hist = [
        {"role": "user", "content": "实现登录和支付"},
        {"role": "assistant", "content": "我先看代码"},
        {"role": "user", "content": "继续"},
    ]
    brief = worker_brief_messages("改 auth.py 登录校验", "", None, hist)
    assert len(brief) == 1
    text = brief[0]["content"]
    assert "Your only task" in text
    assert "改 auth.py 登录校验" in text
    assert "我先看代码" not in text
    roles = [m["role"] for m in brief]
    assert roles == ["user"]


def test_worker_loop_history_is_brief_not_snapshot():
    snap = [
        {"role": "user", "content": "把仓库里所有文件重写一遍"},
        {"role": "assistant", "content": "好的马上做"},
    ]
    loop = SubAgentLoop(
        session_id="ut_brief",
        subagent_id="wk_brief",
        kind="worker",
        task="只列出 workspace 顶层文件名",
        behavior_desc="",
        snapshot=snap,
        summary=None,
        broadcaster=None,
        main_enqueue=None,
    )
    assert all(
        "把仓库里所有文件重写一遍" not in str(m.get("content")) or "Overall user goal" in str(m.get("content"))
        for m in loop.history
    )
    assert any("只列出 workspace 顶层文件名" in str(m.get("content")) for m in loop.history)
    assert len(loop.history) == 1


def test_heuristic_spawn_uses_narrow_tasks():
    ag = AgentLoop("ut_h_spawn")
    res = ag._heuristic_fallback([{"role": "user", "content": "请 spawn_workers 处理当前需求"}])
    tasks = res["tool_calls"][0]["args"]["tasks"]
    assert tasks[0] != tasks[1]
    goal = "请 spawn_workers 处理当前需求"
    assert all(t not in goal for t in tasks)


def test_heuristic_batch_word_does_not_spawn():
    ag = AgentLoop("ut_h_batch")
    res = ag._heuristic_fallback([{"role": "user", "content": "批量整理一下桌面文件名"}])
    names = [tc["name"] for tc in res.get("tool_calls") or []]
    assert "spawn_worker" not in names
    assert "spawn_workers" not in names


def test_parse_worker_requires_done_when():
    specs, err = parse_worker_specs("spawn_worker", {"task": "改 auth.py"})
    assert specs == []
    assert err and "done_when" in err


def test_parse_worker_ok():
    specs, err = parse_worker_specs(
        "spawn_worker",
        {"task": "改 auth.py 校验", "done_when": "登录校验有单测", "files": ["auth.py"], "mode": "implement"},
    )
    assert err is None
    assert len(specs) == 1
    assert specs[0].done_when.startswith("登录")
    assert specs[0].files == ["auth.py"]


def test_parse_workers_parallel_done_when():
    specs, err = parse_worker_specs(
        "spawn_workers",
        {
            "tasks": ["改 auth.py", "改 pay.py"],
            "done_when": ["登录通过", "支付签名校验通过"],
            "mode": "implement",
        },
    )
    assert err is None
    assert [s.task for s in specs] == ["改 auth.py", "改 pay.py"]
    assert specs[0].done_when == "登录通过"


def test_history_has_readonly_explore():
    assert history_has_readonly_explore([]) is False
    assert history_has_readonly_explore([{"role": "tool", "name": "glob", "content": "[错误] x"}]) is False
    assert history_has_readonly_explore([{"role": "tool", "name": "glob", "content": "a.txt"}]) is True


def test_path_in_scope():
    assert path_in_scope("auth.py", ["auth.py"]) is True
    assert path_in_scope("pkg/a.py", ["pkg"]) is True
    assert path_in_scope("other.py", ["auth.py"]) is False
    assert path_in_scope("x.py", []) is True


def test_validate_rejects_overlapping_files():
    hist = [{"role": "user", "content": "实现登录和支付"}]
    msg = validate_worker_tasks(
        [
            WorkerSpec("改登录", "登录过", ["auth.py"], "implement"),
            WorkerSpec("改支付", "支付过", ["auth.py"], "implement"),
        ],
        hist,
    )
    assert msg and "files" in msg


def test_worker_brief_includes_done_when():
    brief = worker_brief_messages(
        "改 auth.py",
        "",
        None,
        [{"role": "user", "content": "实现登录和支付"}],
        done_when="校验函数有单测",
        files=["auth.py"],
        mode="explore",
    )
    text = brief[0]["content"]
    assert "Done when" in text
    assert "Allowed files" in text
    assert "explore" in text.lower()
