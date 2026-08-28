"""files 单元测试 — 读写与越权拒绝 (M1 沙箱)"""

import uuid

from app.tools.files import apply_edit, apply_write, edit, glob, grep, read, write

_UNIQUE = f"test_{uuid.uuid4().hex[:8]}"


def test_write_read_roundtrip():
    path = f"{_UNIQUE}.txt"
    r1 = write.invoke({"path": path, "content": "hello harness"})
    assert "已写入" in r1
    r2 = read.invoke({"path": path})
    assert "hello harness" in r2


def test_read_missing():
    r = read.invoke({"path": "no_such_file_xyz.txt"})
    assert "文件不存在" in r


def test_path_escape_rejected():
    # 越权路径直接拒绝，不再静默重映射
    assert "越权" in read.invoke({"path": "../etc/passwd"})
    assert "越权" in write.invoke({"path": "../escape.txt", "content": "x"})
    assert "越权" in glob.invoke({"pattern": "../**/*"})
    assert "越权" in grep.invoke({"pattern": "root", "path": "../etc/passwd"})


def test_apply_write_includes_old_new_diff():
    path = f"{_UNIQUE}_diff.txt"
    msg1, extra1 = apply_write(path, "alpha")
    assert "已写入" in msg1
    assert extra1 and extra1["diff"]["old_text"] == ""
    assert extra1["diff"]["new_text"] == "alpha"
    _msg2, extra2 = apply_write(path, "beta")
    assert extra2 and extra2["diff"]["old_text"] == "alpha"
    assert extra2["diff"]["new_text"] == "beta"


def test_edit_unique_and_ambiguous():
    path = f"{_UNIQUE}_edit.txt"
    write.invoke({"path": path, "content": "foo bar foo"})
    bad = edit.invoke({"path": path, "old_string": "foo", "new_string": "baz"})
    assert "多处" in bad
    ok, extra = apply_edit(path, "foo bar foo", "baz")
    assert "已编辑" in ok
    assert extra and "baz" in extra["diff"]["new_text"]
    assert read.invoke({"path": path}) == "baz"


def test_glob_embedded_traversal_filtered():
    # 内嵌 ../ 的模式不允许枚举 WORKDIR 外文件（即使不以 ../ 开头）
    sub = f"sub_{uuid.uuid4().hex[:8]}"
    assert "已写入" in write.invoke({"path": f"{sub}/inner.txt", "content": "x"})
    try:
        # sub/../.. 回到 WORKDIR 上一级（项目根），README.md 存在 — 必须被过滤
        r = glob.invoke({"pattern": f"{sub}/../../README.md"})
        assert r == "(无匹配)"
        # 正常子目录匹配不受影响
        r2 = glob.invoke({"pattern": f"{sub}/*.txt"})
        assert f"{sub}/inner.txt" in r2
    finally:
        import shutil

        from app.tools.files import _workdir

        shutil.rmtree(_workdir() / sub, ignore_errors=True)