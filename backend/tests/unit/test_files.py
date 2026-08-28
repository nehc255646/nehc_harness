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