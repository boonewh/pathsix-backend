"""
Pydantic validation schemas for Client entity.
Step 1A of validation implementation.
"""

from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field, EmailStr, ConfigDict, field_validator
from app.constants import PHONE_LABELS


class ClientCreateSchema(BaseModel):
    """Schema for creating a new client via POST /api/clients"""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    # Required fields
    name: str = Field(..., min_length=1, max_length=100, description="Company/Client name")

    # Optional fields
    contact_person: Optional[str] = Field(None, max_length=100, description="Primary contact person name")
    contact_title: Optional[str] = Field(None, max_length=100, description="Primary contact person title")
    email: Optional[EmailStr] = Field(None, description="Primary contact email address")
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
    status: Optional[str] = Field("new", description="Client status")

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: Optional[str]) -> str:
        """Normalize type field - permissive validation (accepts any string)"""
        if value is None or value.strip() == "":
            return "None"
        return value.strip()

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: Optional[str]) -> str:
        """Normalize status field - permissive validation (accepts any string)"""
        if value is None or value.strip() == "":
            return "new"
        return value.strip()

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
    """Schema for updating an existing client via PUT /api/clients/{id}"""

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
    status: Optional[str] = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: Optional[str]) -> Optional[str]:
        """Normalize type field - permissive validation (accepts any string)"""
        if value is None or value.strip() == "":
            return value
        return value.strip()

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: Optional[str]) -> Optional[str]:
        """Normalize status field - permissive validation (accepts any string)"""
        if value is None or value.strip() == "":
            return value
        return value.strip()

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


class ClientAssignSchema(BaseModel):
    """Schema for assigning a client to a user via PUT /api/clients/{id}/assign"""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    assigned_to: int = Field(..., gt=0, description="User ID to assign the client to")


class ClientResponseSchema(BaseModel):
    """Schema for client data in API responses"""

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
    interaction_count: Optional[int] = None
    last_interaction_date: Optional[str] = None


class ClientListResponseSchema(BaseModel):
    """Schema for paginated client list responses"""

    model_config = ConfigDict(from_attributes=True)

    clients: list[ClientResponseSchema]
    total: int
    page: int
    per_page: int
    sort_order: str
