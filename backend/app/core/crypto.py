"""Fernet 加解密 — Provider.api_key 落库"""

import logging

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
