"""Pydantic validation schemas for Project entity."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.constants import PHONE_LABELS, PROJECT_STATUS_OPTIONS, TYPE_OPTIONS


class ProjectCreateSchema(BaseModel):
    """Schema for creating a project."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    project_name: str = Field(..., min_length=1, max_length=255)
    project_status: str = Field("pending")
    type: Optional[str] = Field("None")
    project_description: Optional[str] = None
    notes: Optional[str] = None
    project_start: Optional[datetime] = None
    project_end: Optional[datetime] = None
    project_worth: Optional[float] = Field(None, ge=0)
    client_id: Optional[int] = None
    lead_id: Optional[int] = None
    primary_contact_name: Optional[str] = Field(None, max_length=100)
    primary_contact_title: Optional[str] = Field(None, max_length=100)
    primary_contact_email: Optional[EmailStr] = None
    primary_contact_phone: Optional[str] = Field(None, max_length=20)
    primary_contact_phone_label: Optional[str] = Field("work")

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: Optional[str]) -> str:
        if value is None or value.strip() == "":
            return "None"
        if value not in TYPE_OPTIONS:
            raise ValueError(f"type must be one of: {', '.join(TYPE_OPTIONS)}")
        return value

    @field_validator("project_status")
    @classmethod
    def validate_status(cls, value: Optional[str]) -> str:
        if value is None or value.strip() == "":
            return PROJECT_STATUS_OPTIONS[0]
        if value not in PROJECT_STATUS_OPTIONS:
            raise ValueError(f"project_status must be one of: {', '.join(PROJECT_STATUS_OPTIONS)}")
        return value

    @field_validator("primary_contact_phone_label")
    @classmethod
    def validate_phone_label(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if value not in PHONE_LABELS:
            raise ValueError(f"phone label must be one of: {', '.join(PHONE_LABELS)}")
        return value

    @field_validator("primary_contact_phone", mode="before")
    @classmethod
    def normalize_phone(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            return value or None
        raise TypeError("phone values must be strings")


class ProjectUpdateSchema(BaseModel):
    """Schema for updating a project."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    project_name: Optional[str] = Field(None, min_length=1, max_length=255)
    project_status: Optional[str] = None
    type: Optional[str] = None
    project_description: Optional[str] = None
    notes: Optional[str] = None
    project_start: Optional[datetime] = None
    project_end: Optional[datetime] = None
    project_worth: Optional[float] = Field(None, ge=0)
    client_id: Optional[int] = None
    lead_id: Optional[int] = None
    primary_contact_name: Optional[str] = Field(None, max_length=100)
    primary_contact_title: Optional[str] = Field(None, max_length=100)
    primary_contact_email: Optional[EmailStr] = None
    primary_contact_phone: Optional[str] = Field(None, max_length=20)
    primary_contact_phone_label: Optional[str] = None
    assigned_to: Optional[int] = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value.strip() == "":
            return value
        if value not in TYPE_OPTIONS:
            raise ValueError(f"type must be one of: {', '.join(TYPE_OPTIONS)}")
        return value

    @field_validator("project_status")
    @classmethod
    def validate_status(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value.strip() == "":
            return value
        if value not in PROJECT_STATUS_OPTIONS:
            raise ValueError(f"project_status must be one of: {', '.join(PROJECT_STATUS_OPTIONS)}")
        return value

    @field_validator("primary_contact_phone_label")
    @classmethod
    def validate_phone_label(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if value not in PHONE_LABELS:
            raise ValueError(f"phone label must be one of: {', '.join(PHONE_LABELS)}")
        return value

    @field_validator("primary_contact_phone", mode="before")
    @classmethod
    def normalize_phone(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            return value or None
        raise TypeError("phone values must be strings")


class ProjectResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_name: str
    type: Optional[str]
    project_status: str
    project_description: Optional[str]
    notes: Optional[str]
    project_start: Optional[datetime]
    project_end: Optional[datetime]
    project_worth: Optional[float]
    client_id: Optional[int]
    lead_id: Optional[int]
    client_name: Optional[str]
    lead_name: Optional[str]
    created_at: Optional[datetime]
    primary_contact_name: Optional[str]
    primary_contact_title: Optional[str]
    primary_contact_email: Optional[str]
    primary_contact_phone: Optional[str]
    primary_contact_phone_label: Optional[str]


class ProjectListResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    projects: list[ProjectResponseSchema]
    total: int
    page: int
    per_page: int
    sort_order: str
