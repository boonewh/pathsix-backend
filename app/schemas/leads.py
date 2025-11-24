"""
Pydantic validation schemas for Lead entity.
Step 1A of validation implementation.
"""

from typing import Optional, Literal
from datetime import datetime
from pydantic import BaseModel, Field, EmailStr, validator
from app.constants import LEAD_STATUS_OPTIONS, TYPE_OPTIONS, PHONE_LABELS


class LeadCreateSchema(BaseModel):
    """Schema for creating a new lead via POST /api/leads"""
    
    # Required fields
    name: str = Field(..., min_length=1, max_length=100, description="Company name")
    
    # Optional fields
    contact_person: Optional[str] = Field(None, max_length=100, description="Contact person name")
    contact_title: Optional[str] = Field(None, max_length=100, description="Contact person title")
    email: Optional[EmailStr] = Field(None, description="Contact email address")
    phone: Optional[str] = Field(None, max_length=20, description="Primary phone number")
    phone_label: Optional[Literal["work", "mobile", "home", "fax", "other"]] = Field("work", description="Primary phone label")
    secondary_phone: Optional[str] = Field(None, max_length=20, description="Secondary phone number")
    secondary_phone_label: Optional[Literal["work", "mobile", "home", "fax", "other"]] = Field(None, description="Secondary phone label")
    address: Optional[str] = Field(None, max_length=255, description="Street address")
    city: Optional[str] = Field(None, max_length=100, description="City")
    state: Optional[str] = Field(None, max_length=100, description="State/Province")
    zip: Optional[str] = Field(None, max_length=20, description="ZIP/Postal code")
    notes: Optional[str] = Field(None, description="Additional notes")
    type: Optional[str] = Field("None", description="Business type/category")
    lead_status: Optional[Literal["open", "qualified", "proposal", "closed"]] = Field("open", description="Lead status")

    @validator("type")
    def validate_type(cls, v):
        if v not in TYPE_OPTIONS:
            return "None"
        return v

    @validator("phone", "secondary_phone")
    def validate_phone(cls, v):
        if v is None or v.strip() == "":
            return None
        # Basic phone validation - will be cleaned by phone_utils
        return v.strip()

    class Config:
        str_strip_whitespace = True
        validate_assignment = True


class LeadUpdateSchema(BaseModel):
    """Schema for updating an existing lead via PUT /api/leads/{id}"""
    
    # All fields optional for updates
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    contact_person: Optional[str] = Field(None, max_length=100)
    contact_title: Optional[str] = Field(None, max_length=100)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=20)
    phone_label: Optional[Literal["work", "mobile", "home", "fax", "other"]] = None
    secondary_phone: Optional[str] = Field(None, max_length=20)
    secondary_phone_label: Optional[Literal["work", "mobile", "home", "fax", "other"]] = None
    address: Optional[str] = Field(None, max_length=255)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    zip: Optional[str] = Field(None, max_length=20)
    notes: Optional[str] = None
    type: Optional[str] = None
    lead_status: Optional[Literal["open", "qualified", "proposal", "closed"]] = None

    @validator("type")
    def validate_type(cls, v):
        if v is not None and v not in TYPE_OPTIONS:
            return "None"
        return v

    @validator("phone", "secondary_phone")
    def validate_phone(cls, v):
        if v is None or v.strip() == "":
            return None
        return v.strip()

    class Config:
        str_strip_whitespace = True
        validate_assignment = True


class LeadResponseSchema(BaseModel):
    """Schema for lead data in API responses"""
    
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

    class Config:
        from_attributes = True  # For SQLAlchemy model conversion


class LeadListResponseSchema(BaseModel):
    """Schema for paginated lead list responses"""
    
    leads: list[LeadResponseSchema]
    total: int
    page: int
    per_page: int
    sort_order: str

    class Config:
        from_attributes = True


class LeadAssignSchema(BaseModel):
    """Schema for assigning leads to users"""
    
    assigned_to: int = Field(..., description="User ID to assign the lead to")

    class Config:
        validate_assignment = True