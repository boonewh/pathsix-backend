"""
Pydantic validation schemas for the PathSix CRM API.

This module contains all Pydantic schemas for request validation and response serialization.
As per Step 1 of the implementation plan, these schemas serve as the source of truth
for validation rules that will be mirrored in frontend Zod schemas.
"""

from .leads import (
    LeadCreateSchema,
    LeadUpdateSchema,
    LeadResponseSchema,
    LeadListResponseSchema,
    LeadAssignSchema
)

__all__ = [
    "LeadCreateSchema",
    "LeadUpdateSchema", 
    "LeadResponseSchema",
    "LeadListResponseSchema",
    "LeadAssignSchema"
]