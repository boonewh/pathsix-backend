"""Pydantic validation schemas for Client entity."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.constants import CLIENT_STATUS_OPTIONS, PHONE_LABELS, TYPE_OPTIONS


class ClientCreateSchema(BaseModel):
    """Schema for creating a new client."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    name: str = Field(..., min_length=1, max_length=100)
    contact_person: Optional[str] = Field(None, max_length=100)
    contact_title: Optional[str] = Field(None, max_length=100)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=20)
    phone_label: Optional[str] = Field("work")
    secondary_phone: Optional[str] = Field(None, max_length=20)
    secondary_phone_label: Optional[str] = None
    address: Optional[str] = Field(None, max_length=255)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    zip: Optional[str] = Field(None, max_length=20)
    notes: Optional[str] = None
    type: Optional[str] = Field("None")
    status: Optional[str] = Field("new")

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: Optional[str]) -> str:
        if value is None or value.strip() == "":
            return "None"
        if value not in TYPE_OPTIONS:
            raise ValueError(f"type must be one of: {', '.join(TYPE_OPTIONS)}")
        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: Optional[str]) -> str:
        if value is None or value.strip() == "":
            return "new"
        if value not in CLIENT_STATUS_OPTIONS:
            raise ValueError(f"status must be one of: {', '.join(CLIENT_STATUS_OPTIONS)}")
        return value

    @field_validator("phone_label", "secondary_phone_label")
    @classmethod
    def validate_phone_labels(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if value not in PHONE_LABELS:
            raise ValueError(f"phone label must be one of: {', '.join(PHONE_LABELS)}")
        return value

    @field_validator("phone", "secondary_phone", mode="before")
    @classmethod
    def normalize_phone(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            return value or None
        raise TypeError("phone values must be strings")


class ClientUpdateSchema(BaseModel):
    """Schema for updating an existing client."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    contact_person: Optional[str] = Field(None, max_length=100)
    contact_title: Optional[str] = Field(None, max_length=100)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=20)
    phone_label: Optional[str] = None
    secondary_phone: Optional[str] = Field(None, max_length=20)
    secondary_phone_label: Optional[str] = None
    address: Optional[str] = Field(None, max_length=255)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    zip: Optional[str] = Field(None, max_length=20)
    notes: Optional[str] = None
    type: Optional[str] = None
    status: Optional[str] = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value.strip() == "":
            return value
        if value not in TYPE_OPTIONS:
            raise ValueError(f"type must be one of: {', '.join(TYPE_OPTIONS)}")
        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value.strip() == "":
            return value
        if value not in CLIENT_STATUS_OPTIONS:
            raise ValueError(f"status must be one of: {', '.join(CLIENT_STATUS_OPTIONS)}")
        return value

    @field_validator("phone_label", "secondary_phone_label")
    @classmethod
    def validate_phone_labels(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if value not in PHONE_LABELS:
            raise ValueError(f"phone label must be one of: {', '.join(PHONE_LABELS)}")
        return value

    @field_validator("phone", "secondary_phone", mode="before")
    @classmethod
    def normalize_phone(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            return value or None
        raise TypeError("phone values must be strings")


class ClientResponseSchema(BaseModel):
    """Schema for serializing client responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    contact_person: Optional[str]
    contact_title: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    phone_label: Optional[str]
    secondary_phone: Optional[str]
    secondary_phone_label: Optional[str]
    address: Optional[str]
    city: Optional[str]
    state: Optional[str]
    zip: Optional[str]
    notes: Optional[str]
    type: Optional[str]
    status: str
    created_at: datetime
    assigned_to: Optional[int]
    assigned_to_name: Optional[str]


class ClientListResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    clients: list[ClientResponseSchema]
    total: int
    page: int
    per_page: int
    sort_order: str
    activity_filter: Optional[str]
