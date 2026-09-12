"""Validated account inputs; blank dates retain the existing default/no-change behavior."""

from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, field_validator


class AccountFields(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    account_name: str | None = Field(None, max_length=255)
    status: str | None = None
    opened_on: datetime | None = None
    notes: str | None = None

    @field_validator("opened_on", mode="before")
    @classmethod
    def parse_date(cls, value):
        if value is None or value == "":
            return None
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if not isinstance(value, datetime):
            raise ValueError("Invalid opened_on format")
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value


class AccountCreateSchema(AccountFields):
    client_id: int = Field(gt=0, strict=True)
    account_number: str = Field(min_length=1, max_length=100)


class AccountUpdateSchema(AccountFields):
    client_id: int | None = Field(None, gt=0, strict=True)
    account_number: str | None = Field(None, min_length=1, max_length=100)
