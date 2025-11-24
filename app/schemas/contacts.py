"""Pydantic validation schemas for Contact entity."""
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.constants import PHONE_LABELS


class ContactCreateSchema(BaseModel):
    """Schema for creating a contact."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    client_id: Optional[int] = None
    lead_id: Optional[int] = None
    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    title: Optional[str] = Field(None, max_length=100)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=20)
    phone_label: Optional[str] = Field("work")
    secondary_phone: Optional[str] = Field(None, max_length=20)
    secondary_phone_label: Optional[str] = None
    notes: Optional[str] = None

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


class ContactUpdateSchema(BaseModel):
    """Schema for updating a contact."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    client_id: Optional[int] = None
    lead_id: Optional[int] = None
    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    title: Optional[str] = Field(None, max_length=100)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=20)
    phone_label: Optional[str] = None
    secondary_phone: Optional[str] = Field(None, max_length=20)
    secondary_phone_label: Optional[str] = None
    notes: Optional[str] = None

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
