"""
Pydantic validation schemas for Project entity.
Step 1A of validation implementation.
"""

from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field, EmailStr, ConfigDict, field_validator
from app.constants import PHONE_LABELS


class ProjectCreateSchema(BaseModel):
    """Schema for creating a new project via POST /api/projects"""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    # Required fields
    project_name: str = Field(..., min_length=1, max_length=255, description="Project name")

    # Entity relationships (one of client_id or lead_id should be provided)
    client_id: Optional[int] = Field(None, gt=0, description="Associated client ID")
    lead_id: Optional[int] = Field(None, gt=0, description="Associated lead ID")

    # Optional fields
    project_description: Optional[str] = Field(None, description="Project description")
    type: Optional[str] = Field("None", description="Project type/category")
    project_status: Optional[str] = Field("pending", description="Project status")
    project_start: Optional[datetime] = Field(None, description="Project start date")
    project_end: Optional[datetime] = Field(None, description="Project end date")
    project_worth: Optional[float] = Field(None, ge=0, description="Project value/worth")
    notes: Optional[str] = Field(None, description="Additional project notes")

    # Primary contact fields
    primary_contact_name: Optional[str] = Field(None, max_length=100, description="Primary contact name")
    primary_contact_title: Optional[str] = Field(None, max_length=100, description="Primary contact title")
    primary_contact_email: Optional[EmailStr] = Field(None, description="Primary contact email")
    primary_contact_phone: Optional[str] = Field(None, max_length=20, description="Primary contact phone")
    primary_contact_phone_label: Optional[str] = Field("work", description="Primary contact phone label")

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: Optional[str]) -> str:
        """Normalize type field - permissive validation (accepts any string)"""
        if value is None or value.strip() == "":
            return "None"
        return value.strip()

    @field_validator("project_status")
    @classmethod
    def validate_project_status(cls, value: Optional[str]) -> str:
        """Normalize project_status field - permissive validation (accepts any string)"""
        if value is None or value.strip() == "":
            return "pending"
        return value.strip()

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

    @field_validator("project_end")
    @classmethod
    def validate_project_dates(cls, end_date: Optional[datetime], values):
        # Note: In Pydantic v2, we need to use model_validator for cross-field validation
        # This basic validator just ensures the date is valid if provided
        return end_date


class ProjectUpdateSchema(BaseModel):
    """Schema for updating an existing project via PUT /api/projects/{id}"""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    # All fields optional for updates
    project_name: Optional[str] = Field(None, min_length=1, max_length=255)
    client_id: Optional[int] = Field(None, gt=0)
    lead_id: Optional[int] = Field(None, gt=0)
    project_description: Optional[str] = None
    type: Optional[str] = None
    project_status: Optional[str] = None
    project_start: Optional[datetime] = None
    project_end: Optional[datetime] = None
    project_worth: Optional[float] = Field(None, ge=0)
    notes: Optional[str] = None
    primary_contact_name: Optional[str] = Field(None, max_length=100)
    primary_contact_title: Optional[str] = Field(None, max_length=100)
    primary_contact_email: Optional[EmailStr] = None
    primary_contact_phone: Optional[str] = Field(None, max_length=20)
    primary_contact_phone_label: Optional[str] = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: Optional[str]) -> Optional[str]:
        """Normalize type field - permissive validation (accepts any string)"""
        if value is None or value.strip() == "":
            return value
        return value.strip()

    @field_validator("project_status")
    @classmethod
    def validate_project_status(cls, value: Optional[str]) -> Optional[str]:
        """Normalize project_status field - permissive validation (accepts any string)"""
        if value is None or value.strip() == "":
            return value
        return value.strip()

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
    """Schema for project data in API responses"""

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
    """Schema for paginated project list responses"""

    model_config = ConfigDict(from_attributes=True)

    projects: list[ProjectResponseSchema]
    total: int
    page: int
    per_page: int
    sort_order: str
