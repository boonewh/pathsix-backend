"""
Pydantic validation schemas for Lead entity.
Step 1A of validation implementation.
"""

from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field, EmailStr, ConfigDict, field_validator
from app.constants import LEAD_STATUS_OPTIONS, TYPE_OPTIONS, PHONE_LABELS


class LeadCreateSchema(BaseModel):
    """Schema for creating a new lead via POST /api/leads"""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    # Required fields
    name: str = Field(..., min_length=1, max_length=100, description="Company name")

    # Optional fields
    contact_person: Optional[str] = Field(None, max_length=100, description="Contact person name")
    contact_title: Optional[str] = Field(None, max_length=100, description="Contact person title")
    email: Optional[EmailStr] = Field(None, description="Contact email address")
    phone: Optional[str] = Field(None, max_length=20, description="Primary phone number")
    phone_label: Optional[str] = Field("work", description="Primary phone label")
    secondary_phone: Optional[str] = Field(None, max_length=20, description="Secondary phone number")
    secondary_phone_label: Optional[str] = Field(None, description="Secondary phone label")
    address: Optional[str] = Field(None, max_length=255, description="Street address")
    city: Optional[str] = Field(None, max_length=100, description="City")
    state: Optional[str] = Field(None, max_length=100, description="State/Province")
    zip: Optional[str] = Field(None, max_length=20, description="ZIP/Postal code")
    notes: Optional[str] = Field(None, description="Additional notes")
    type: Optional[str] = Field("None", description="Business type/category")
    lead_status: Optional[str] = Field("open", description="Lead status")

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: Optional[str]) -> str:
        if value is None or value.strip() == "":
            return "None"
        if value not in TYPE_OPTIONS:
            raise ValueError(f"type must be one of: {', '.join(TYPE_OPTIONS)}")
        return value

    @field_validator("lead_status")
    @classmethod
    def validate_lead_status(cls, value: Optional[str]) -> str:
        if value is None or value.strip() == "":
            return "open"
        if value not in LEAD_STATUS_OPTIONS:
            raise ValueError(f"lead_status must be one of: {', '.join(LEAD_STATUS_OPTIONS)}")
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


class LeadUpdateSchema(BaseModel):
    """Schema for updating an existing lead via PUT /api/leads/{id}"""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    # All fields optional for updates
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
    lead_status: Optional[str] = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value.strip() == "":
            return value
        if value not in TYPE_OPTIONS:
            raise ValueError(f"type must be one of: {', '.join(TYPE_OPTIONS)}")
        return value

    @field_validator("lead_status")
    @classmethod
    def validate_lead_status(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value.strip() == "":
            return value
        if value not in LEAD_STATUS_OPTIONS:
            raise ValueError(f"lead_status must be one of: {', '.join(LEAD_STATUS_OPTIONS)}")
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


class LeadResponseSchema(BaseModel):
    """Schema for lead data in API responses"""

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
    lead_status: str
    created_at: datetime
    converted_on: Optional[datetime]
    assigned_to: Optional[int]
    assigned_to_name: Optional[str]


class LeadListResponseSchema(BaseModel):
    """Schema for paginated lead list responses"""

    model_config = ConfigDict(from_attributes=True)

    leads: list[LeadResponseSchema]
    total: int
    page: int
    per_page: int
    sort_order: str


class LeadAssignSchema(BaseModel):
    """Schema for assigning leads to users"""

    assigned_to: int = Field(..., description="User ID to assign the lead to")

    model_config = ConfigDict(validate_assignment=True)
