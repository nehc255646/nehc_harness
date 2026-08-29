from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

PROVIDER_SLUG = r"^[a-z][a-z0-9_-]{0,62}$"
ENV_NAME = r"^[A-Za-z_][A-Za-z0-9_]*$"


def _normalize_env_name(value: str | None) -> str | None:
    name = (value or "").strip()
    if not name:
        return None
    if not re.match(ENV_NAME, name) or len(name) > 128:
        raise ValueError("环境变量名仅允许字母、数字和下划线，且不能以数字开头")
    return name


class ProviderCreate(BaseModel):
    provider_id: str = Field(pattern=PROVIDER_SLUG, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=512)
    api_key: str = ""
    api_key_from_env: bool = False
    api_key_env: str | None = None

    @field_validator("api_key_env")
    @classmethod
    def _env_name(cls, v: str | None) -> str | None:
        return _normalize_env_name(v)

    @model_validator(mode="after")
    def _env_required(self):
        if self.api_key_from_env and not self.api_key_env:
            raise ValueError("从环境变量读取时请填写变量名")
        if not self.api_key_from_env:
            self.api_key_env = None
        return self


class ProviderUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, min_length=1, max_length=512)
    api_key: str | None = None
    api_key_from_env: bool | None = None
    api_key_env: str | None = None

    @field_validator("api_key_env")
    @classmethod
    def _env_name(cls, v: str | None) -> str | None:
        return _normalize_env_name(v)

    @model_validator(mode="after")
    def _env_required(self):
        if self.api_key_from_env is True and self.api_key_env is None:
            # 未带 api_key_env 字段时保持原值；显式空字符串已被规范化为 None
            dump = self.model_dump(exclude_unset=True)
            if "api_key_env" in dump and dump["api_key_env"] is None:
                raise ValueError("从环境变量读取时请填写变量名")
        if self.api_key_from_env is False:
            self.api_key_env = None
        return self


class ProviderOut(BaseModel):
    id: int
    provider_id: str
    display_name: str
    base_url: str
    api_key_set: bool
    api_key_from_env: bool = False
    api_key_env: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ModelCreate(BaseModel):
    model_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    context_window: int = Field(default=128000, ge=1024)
    temperature: float = Field(default=0.2, ge=0, le=2)
    request_thinking: bool = False
    reasoning_effort: str | None = Field(default=None, max_length=32)


class ModelUpdate(BaseModel):
    model_id: str | None = Field(default=None, min_length=1, max_length=128)
    display_name: str | None = None
    context_window: int | None = Field(default=None, ge=1024)
    temperature: float | None = Field(default=None, ge=0, le=2)
    request_thinking: bool | None = None
    reasoning_effort: str | None = Field(default=None, max_length=32)


class ModelOut(BaseModel):
    id: int
    provider_id: int
    provider_slug: str | None = None
    provider_name: str | None = None
    model_id: str
    display_name: str
    context_window: int
    temperature: float
    request_thinking: bool = False
    reasoning_effort: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    model_id: int | None = None
    work_mode: str = Field(default="auto", pattern="^(auto|plan)$")


class SessionUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    model_id: int | None = None
    status: str | None = None
    work_mode: str | None = Field(default=None, pattern="^(auto|plan)$")


class SessionOut(BaseModel):
    id: str
    title: str
    status: str
    model_id: int | None
    work_mode: str = "auto"
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageOut(BaseModel):
    id: int
    public_id: str
    session_id: str
    agent_id: str
    role: str
    content: Any
    tool_call_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ToolLogOut(BaseModel):
    id: int
    session_id: str
    message_id: int | None
    tool_call_id: str
    agent_id: str
    name: str
    args: Any
    result: Any
    is_error: bool
    duration_ms: int | None
    rule_hit: str | None
    decision: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DefaultModelBody(BaseModel):
    default_model_id: int | None = None


class ProviderTestBody(BaseModel):
    model_id: str = Field(min_length=1, max_length=128)


class LlmProbeBody(BaseModel):
    """用当前表单值探测某个模型，不必先保存。"""

    base_url: str = Field(min_length=1, max_length=512)
    model_id: str = Field(min_length=1, max_length=128)
    api_key: str | None = None
    api_key_from_env: bool = False
    api_key_env: str | None = None
    provider_id: int | None = None
    provider_slug: str | None = None

    @field_validator("api_key_env")
    @classmethod
    def _env_name(cls, v: str | None) -> str | None:
        return _normalize_env_name(v)
