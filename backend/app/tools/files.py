"""文件工具 — read/write/edit/glob/grep (M1 实现)"""

import glob as glob_module
import logging
import re
from pathlib import Path

from langchain_core.tools import tool

from app.core.config import settings

logger = logging.getLogger("harness.files")


def _workdir() -> Path:
    p = Path(settings.workdir)
    if not p.is_absolute():
        # backend/app/tools -> backend -> project root
        p = (Path(__file__).resolve().parents[3] / settings.workdir).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _resolve(path: str) -> Path | None:
    """约束在 WORKDIR 内，越权路径直接拒绝 (返回 None)"""
    wd = _workdir()
    target = (wd / path).resolve()
    try:
        target.relative_to(wd)
    except ValueError:
        return None
    return target


@tool
def read(path: str) -> str:
    """读取文件内容。参数: path (相对于 WORKDIR)"""
    target = _resolve(path)
    if target is None:
        return f"[错误] 越权路径: {path}"
    if not target.exists():
        return f"[错误] 文件不存在: {path}"
    if target.is_dir():
        return f"[错误] 是目录而非文件: {path}"
    try:
        # 大文件截断提示
        content = target.read_text(encoding="utf-8", errors="ignore")
        if len(content) > 50000:
            content = content[:50000] + f"\n\n...[截断，文件过大，共 {len(content)} 字符]..."
        return content
    except Exception as e:
        return f"[错误] 读取失败: {e}"


@tool
def write(path: str, content: str) -> str:
    """写入文件（覆盖）。参数: path, content"""
    target = _resolve(path)
    if target is None:
        return f"[错误] 越权路径: {path}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"[成功] 已写入 {path} ({len(content)} 字符)"
    except Exception as e:
        return f"[错误] 写入失败: {e}"


@tool
def edit(path: str, old_string: str, new_string: str) -> str:
    """精确字符串替换。参数: path, old_string, new_string。old_string 必须唯一匹配。"""
    target = _resolve(path)
    if target is None:
        return f"[错误] 越权路径: {path}"
    if not target.exists():
        return f"[错误] 文件不存在: {path}"
    try:
        text = target.read_text(encoding="utf-8", errors="ignore")
        if old_string not in text:
            return "[错误] 未找到 old_string"
        if text.count(old_string) > 1:
            return "[错误] old_string 匹配到多处，请提供更大上下文"
        text = text.replace(old_string, new_string, 1)
        target.write_text(text, encoding="utf-8")
        return f"[成功] 已编辑 {path}"
    except Exception as e:
        return f"[错误] 编辑失败: {e}"


@tool
def glob(pattern: str) -> str:
    """按 glob 匹配文件。参数: pattern (如 **/*.py)"""
    wd = _workdir()
    if pattern.lstrip().startswith(("/", "..")):
        return f"[错误] 越权路径: {pattern}"
    try:
        matches = glob_module.glob(pattern, root_dir=str(wd), recursive=True)
        if not matches:
            return "(无匹配)"
        # 限制输出
        if len(matches) > 200:
            matches = matches[:200]
            return "\n".join(matches) + "\n...[截断，超过 200 条]..."
        return "\n".join(matches)
    except Exception as e:
        return f"[错误] glob 失败: {e}"


@tool
def grep(pattern: str, path: str = ".") -> str:
    """在文件中搜索正则。参数: pattern (正则), path (可选目录/文件前缀)"""
    wd = _workdir()
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"[错误] 正则错误: {e}"
    results: list[str] = []
    search_root = _resolve(path) if path != "." else wd
    if search_root is None:
        return f"[错误] 越权路径: {path}"
    if search_root.is_file():
        files = [search_root]
    else:
        files = list(search_root.rglob("*"))
    for f in files:
        if not f.is_file():
            continue
        # 跳过二进制/大文件
        if f.stat().st_size > 2_000_000:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            logger.debug("grep read failed: %s", e)
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                rel = f.relative_to(wd)
                results.append(f"{rel}:{i}: {line[:300]}")
                if len(results) >= 200:
                    results.append("...[截断，超过 200 条]...")
                    return "\n".join(results)
    if not results:
        return "(无匹配)"
    return "\n".join(results)
