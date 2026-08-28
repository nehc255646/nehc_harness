from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

PROVIDER_SLUG = r"^[a-z][a-z0-9_-]{0,62}$"


class ProviderCreate(BaseModel):
    provider_id: str = Field(pattern=PROVIDER_SLUG, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=512)
    api_key: str = Field(min_length=1)


class ProviderUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, min_length=1, max_length=512)
    api_key: str | None = Field(default=None, min_length=1)


class ProviderOut(BaseModel):
    id: int
    provider_id: str
    display_name: str
    base_url: str
    api_key_set: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ModelCreate(BaseModel):
    model_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    context_window: int = Field(default=128000, ge=1024)
    temperature: float = Field(default=0.2, ge=0, le=2)


class ModelUpdate(BaseModel):
    display_name: str | None = None
    context_window: int | None = Field(default=None, ge=1024)
    temperature: float | None = Field(default=None, ge=0, le=2)


class ModelOut(BaseModel):
    id: int
    provider_id: int
    provider_slug: str | None = None
    provider_name: str | None = None
    model_id: str
    display_name: str
    context_window: int
    temperature: float
    created_at: datetime

    model_config = {"from_attributes": True}


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    model_id: int | None = None


class SessionUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    model_id: int | None = None
    status: str | None = None


class SessionOut(BaseModel):
    id: str
    title: str
    status: str
    model_id: int | None
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
    model_id: str | None = None
