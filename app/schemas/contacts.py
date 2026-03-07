"""
Pydantic validation schemas for Contact entity.
Step 1A of validation implementation.
"""

from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field, EmailStr, ConfigDict, field_validator
from app.constants import PHONE_LABELS


class ContactCreateSchema(BaseModel):
    """Schema for creating a new contact via POST /api/contacts"""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    # Required fields
    first_name: str = Field(..., min_length=1, max_length=100, description="Contact first name")

    # Entity relationships (one of client_id or lead_id should be provided)
    client_id: Optional[int] = Field(None, gt=0, description="Associated client ID")
    lead_id: Optional[int] = Field(None, gt=0, description="Associated lead ID")

    # Optional fields
    last_name: Optional[str] = Field(None, max_length=100, description="Contact last name")
    title: Optional[str] = Field(None, max_length=100, description="Contact job title")
    email: Optional[EmailStr] = Field(None, description="Contact email address")
    phone: Optional[str] = Field(None, max_length=20, description="Primary phone number")
    phone_label: Optional[str] = Field("work", description="Primary phone label")
    secondary_phone: Optional[str] = Field(None, max_length=20, description="Secondary phone number")
    secondary_phone_label: Optional[str] = Field(None, description="Secondary phone label")
    notes: Optional[str] = Field(None, description="Additional notes about the contact")

    @field_validator("phone_label", "secondary_phone_label")
    @classmethod
    def validate_phone_labels(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if value not in PHONE_LABELS:
            raise ValueError(f"phone label must be one of: {', '.join(PHONE_LABELS)}")
        return value

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip() or None
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
    """Schema for updating an existing contact via PUT /api/contacts/{id}"""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    # All fields optional for updates
    client_id: Optional[int] = Field(None, gt=0)
    lead_id: Optional[int] = Field(None, gt=0)
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

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip() or None
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


class ContactResponseSchema(BaseModel):
    """Schema for contact data in API responses"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    client_id: Optional[int]
    lead_id: Optional[int]
    first_name: str
    last_name: Optional[str]
    title: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    phone_label: Optional[str]
    secondary_phone: Optional[str]
    secondary_phone_label: Optional[str]
    notes: Optional[str]
    created_at: datetime


class ContactListResponseSchema(BaseModel):
    """Schema for lists of contacts"""

    model_config = ConfigDict(from_attributes=True)

    contacts: list[ContactResponseSchema]
    total: Optional[int] = None
    page: Optional[int] = None
    per_page: Optional[int] = None
