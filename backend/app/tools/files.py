"""文件工具 — read/write/edit/glob/grep (M1 实现)"""

import glob as glob_module
import logging
import re
from pathlib import Path

from langchain_core.tools import tool

from app.core.config import settings

logger = logging.getLogger("harness.files")

_DIFF_CAP = 20000


def _cap_diff_text(s: str) -> str:
    if len(s) <= _DIFF_CAP:
        return s
    half = _DIFF_CAP // 2
    return s[:half] + f"\n...[截断 {len(s) - _DIFF_CAP} 字符]...\n" + s[-half:]


def make_diff_payload(path: str, old_text: str, new_text: str) -> dict:
    return {"path": path, "old_text": _cap_diff_text(old_text), "new_text": _cap_diff_text(new_text)}


def apply_write(path: str, content: str) -> tuple[str, dict | None]:
    """写入文件。返回 (给模型的文本, 可选 {diff})。"""
    target = _resolve(path)
    if target is None:
        return f"[错误] 越权路径: {path}", None
    try:
        old = ""
        if target.exists() and target.is_file():
            old = target.read_text(encoding="utf-8", errors="ignore")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        msg = f"[成功] 已写入 {path} ({len(content)} 字符)"
        return msg, {"diff": make_diff_payload(path, old, content)}
    except Exception as e:
        return f"[错误] 写入失败: {e}", None


def apply_edit(path: str, old_string: str, new_string: str) -> tuple[str, dict | None]:
    """精确替换。返回 (给模型的文本, 可选 {diff})。"""
    target = _resolve(path)
    if target is None:
        return f"[错误] 越权路径: {path}", None
    if not target.exists():
        return f"[错误] 文件不存在: {path}", None
    try:
        text = target.read_text(encoding="utf-8", errors="ignore")
        if old_string not in text:
            return "[错误] 未找到 old_string", None
        if text.count(old_string) > 1:
            return "[错误] old_string 匹配到多处，请提供更大上下文", None
        new_text = text.replace(old_string, new_string, 1)
        target.write_text(new_text, encoding="utf-8")
        msg = f"[成功] 已编辑 {path}"
        return msg, {"diff": make_diff_payload(path, text, new_text)}
    except Exception as e:
        return f"[错误] 编辑失败: {e}", None


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
    msg, _extra = apply_write(path, content)
    return msg


@tool
def edit(path: str, old_string: str, new_string: str) -> str:
    """精确字符串替换。参数: path, old_string, new_string。old_string 必须唯一匹配。"""
    msg, _extra = apply_edit(path, old_string, new_string)
    return msg


@tool
def glob(pattern: str) -> str:
    """按 glob 匹配文件。参数: pattern (如 **/*.py)"""
    wd = _workdir()
    if pattern.lstrip().startswith(("/", "..")):
        return f"[错误] 越权路径: {pattern}"
    try:
        matches = glob_module.glob(pattern, root_dir=str(wd), recursive=True)
    except Exception as e:
        return f"[错误] glob 失败: {e}"
    # 逐条校验结果仍在 WORKDIR 内（防内嵌 ../ 逃逸），越权条目直接丢弃
    safe: list[str] = []
    for m in matches:
        target = (wd / m).resolve()
        try:
            target.relative_to(wd)
        except ValueError:
            continue
        safe.append(m)
    if not safe:
        return "(无匹配)"
    # 限制输出
    if len(safe) > 200:
        safe = safe[:200]
        return "\n".join(safe) + "\n...[截断，超过 200 条]..."
    return "\n".join(safe)


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
