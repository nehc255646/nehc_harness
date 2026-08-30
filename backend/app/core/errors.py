"""错误码定义"""

from enum import Enum


class ErrorCode(str, Enum):
    MODEL_ERROR = "MODEL_ERROR"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    TOOL_ERROR = "TOOL_ERROR"
    APPROVAL_TIMEOUT = "APPROVAL_TIMEOUT"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    MODEL_NOT_SELECTED = "MODEL_NOT_SELECTED"
    INTERNAL = "INTERNAL"


class NeharnessError(Exception):
    def __init__(self, code: ErrorCode, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")
