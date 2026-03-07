"""
Pydantic validation schemas for Subscription entity.
"""

from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict, field_validator

BILLING_CYCLES = ["monthly", "yearly"]
SUBSCRIPTION_STATUSES = ["active", "paused", "cancelled"]


class SubscriptionCreateSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    client_id: int = Field(..., gt=0, description="Associated client ID")
    plan_name: str = Field(..., min_length=1, max_length=255, description="Plan or service name")
    price: float = Field(..., ge=0, description="Price per billing cycle")
    billing_cycle: str = Field(..., description="Billing cycle: monthly or yearly")
    start_date: datetime = Field(..., description="Subscription start date")
    renewal_date: Optional[datetime] = Field(None, description="Next renewal date (auto-calculated if not provided)")
    status: Optional[str] = Field("active", description="Subscription status")
    notes: Optional[str] = Field(None, description="Additional notes")

    @field_validator("billing_cycle")
    @classmethod
    def validate_billing_cycle(cls, value: str) -> str:
        if value not in BILLING_CYCLES:
            raise ValueError(f"billing_cycle must be one of: {', '.join(BILLING_CYCLES)}")
        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: Optional[str]) -> str:
        if value is None or value.strip() == "":
            return "active"
        if value not in SUBSCRIPTION_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(SUBSCRIPTION_STATUSES)}")
        return value


class SubscriptionUpdateSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    plan_name: Optional[str] = Field(None, min_length=1, max_length=255)
    price: Optional[float] = Field(None, ge=0)
    billing_cycle: Optional[str] = None
    start_date: Optional[datetime] = None
    renewal_date: Optional[datetime] = None
    status: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("billing_cycle")
    @classmethod
    def validate_billing_cycle(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if value not in BILLING_CYCLES:
            raise ValueError(f"billing_cycle must be one of: {', '.join(BILLING_CYCLES)}")
        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if value not in SUBSCRIPTION_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(SUBSCRIPTION_STATUSES)}")
        return value


class SubscriptionResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int
    client_id: int
    client_name: Optional[str]
    plan_name: str
    price: float
    billing_cycle: str
    start_date: datetime
    renewal_date: Optional[datetime]
    status: str
    notes: Optional[str]
    created_at: datetime
    cancelled_at: Optional[datetime]


class SubscriptionListResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    subscriptions: list[SubscriptionResponseSchema]
    total: int
