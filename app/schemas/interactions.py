"""
Pydantic validation schemas for Interaction entity.
Step 1A of validation implementation.
"""

from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field, EmailStr, ConfigDict, field_validator

class InteractionCreateSchema(BaseModel):
    """Schema for creating a new interaction via POST /api/interactions"""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    # Required fields
    contact_date: datetime = Field(..., description="Date and time of the interaction")
    summary: str = Field(..., min_length=1, max_length=255, description="Brief summary of the interaction")

    # Entity relationships (exactly one must be provided)
    client_id: Optional[int] = Field(None, gt=0, description="Associated client ID")
    lead_id: Optional[int] = Field(None, gt=0, description="Associated lead ID")
    project_id: Optional[int] = Field(None, gt=0, description="Associated project ID")

    # Optional fields
    outcome: Optional[str] = Field(None, max_length=255, description="Outcome of the interaction")
    notes: Optional[str] = Field(None, description="Detailed notes about the interaction")
    follow_up: Optional[datetime] = Field(None, description="Follow-up date and time")
    contact_person: Optional[str] = Field(None, max_length=100, description="Person contacted")
    email: Optional[EmailStr] = Field(None, description="Email address used for contact")
    phone: Optional[str] = Field(None, max_length=20, description="Phone number used for contact")

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            return value or None
        raise TypeError("phone value must be a string")


class InteractionUpdateSchema(BaseModel):
    """Schema for updating an existing interaction via PUT /api/interactions/{id}"""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    # All fields optional for updates
    contact_date: Optional[datetime] = Field(None, description="Date and time of the interaction")
    summary: Optional[str] = Field(None, min_length=1, max_length=255, description="Brief summary of the interaction")
    client_id: Optional[int] = Field(None, gt=0, description="Associated client ID")
    lead_id: Optional[int] = Field(None, gt=0, description="Associated lead ID")
    project_id: Optional[int] = Field(None, gt=0, description="Associated project ID")
    outcome: Optional[str] = Field(None, max_length=255, description="Outcome of the interaction")
    notes: Optional[str] = Field(None, description="Detailed notes about the interaction")
    follow_up: Optional[datetime] = Field(None, description="Follow-up date and time")
    contact_person: Optional[str] = Field(None, max_length=100, description="Person contacted")
    email: Optional[EmailStr] = Field(None, description="Email address used for contact")
    phone: Optional[str] = Field(None, max_length=20, description="Phone number used for contact")

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            return value or None
        raise TypeError("phone value must be a string")