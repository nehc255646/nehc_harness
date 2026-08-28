"""files 单元测试 — 读写与越权拒绝 (M1 沙箱)"""

import uuid

from app.tools.files import glob, grep, read, write

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