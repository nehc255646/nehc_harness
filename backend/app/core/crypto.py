"""Fernet 加解密 — Provider.api_key 落库"""

import logging
import os

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

logger = logging.getLogger("harness.crypto")

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet
    key = (settings.encryption_key or "").strip()
    if not key:
        raise RuntimeError("ENCRYPTION_KEY 未配置，无法加解密 api_key")
    try:
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as e:
        raise RuntimeError(f"ENCRYPTION_KEY 非法（需 Fernet 32 字节 urlsafe base64）: {e}") from e
    return _fernet


def encrypt_secret(plain: str) -> str:
    return _get_fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise RuntimeError("api_key 解密失败，检查 ENCRYPTION_KEY 是否与写入时一致") from e


def encryption_ready() -> bool:
    try:
        _get_fernet()
        return True
    except Exception:
        return False


def env_api_key(name: str) -> str:
    """读取指定名称的环境变量；OPENAI_API_KEY 同时回落到 settings。"""
    key = (name or "").strip()
    if not key:
        return ""
    val = (os.environ.get(key) or "").strip()
    if val:
        return val
    if key == "OPENAI_API_KEY":
        return (settings.openai_api_key or "").strip()
    return ""


def provider_api_key(provider) -> str:
    """解析供应商密钥：勾选环境变量时只读配置的变量名。"""
    if getattr(provider, "api_key_from_env", False):
        return env_api_key(getattr(provider, "api_key_env", None) or "")
    return decrypt_secret(getattr(provider, "api_key_encrypted", None) or "")
