"""Shapes of the JSON the API accepts and returns (validated by Pydantic)."""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=200)

    @field_validator("email")
    @classmethod
    def clean_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL_RE.match(value):
            raise ValueError("that doesn't look like an email address")
        return value


class LoginRequest(Credentials):
    device_name: str = Field(default="", max_length=100)


class TokenResponse(BaseModel):
    token: str


class SessionPayload(BaseModel):
    id: str
    subject: str = Field(min_length=1, max_length=200)
    started_at: str = Field(max_length=32)
    duration_seconds: int = Field(ge=0, le=86400)
    status: str = Field(max_length=20)
    updated_at: int = Field(ge=0)
    deleted: bool = False

    @field_validator("id")
    @classmethod
    def must_be_uuid(cls, value: str) -> str:
        return str(uuid.UUID(value))

    @field_validator("started_at")
    @classmethod
    def must_be_iso_time(cls, value: str) -> str:
        datetime.fromisoformat(value)  # raises ValueError if it isn't
        return value


class PushRequest(BaseModel):
    sessions: list[SessionPayload] = Field(max_length=500)


class PushResponse(BaseModel):
    accepted: int
    ignored: int
    cursor: int


class PullResponse(BaseModel):
    sessions: list[SessionPayload]
    cursor: int
    has_more: bool
