"""
Utility functions for data import operations
"""
import re
from typing import Optional, Dict, Any
import pandas as pd
from app.utils.phone_utils import clean_phone_number


def validate_email(email: str) -> Optional[str]:
    """
    Basic email validation and cleaning
    """
    if not email or pd.isna(email):
        return None
    
    email_str = str(email).strip().lower()
    if not email_str:
        return None
    
    # Basic email regex
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if re.match(email_pattern, email_str):
        return email_str
    
    return None

def safe_string_convert(value: Any, max_length: Optional[int] = None) -> Optional[str]:
    """
    Safely convert a value to string, handling NaN and None
    """
    if value is None or pd.isna(value):
        return None
    
    result = str(value).strip()
    if not result:
        return None
    
    if max_length and len(result) > max_length:
        result = result[:max_length]
    
    return result

def validate_required_fields(row: pd.Series, required_fields: list) -> list:
    """
    Validate that required fields are present and not empty
    Returns list of missing field names
    """
    missing = []
    for field in required_fields:
        if field not in row or pd.isna(row[field]) or str(row[field]).strip() == '':
            missing.append(field)
    return missing

def map_lead_data(row: pd.Series) -> Dict[str, Any]:
    """
    Map CSV row data to Lead model fields with validation
    """
    # Validate required fields
    required_fields = ['PLANT_NAME']
    missing_fields = validate_required_fields(row, required_fields)
    if missing_fields:
        raise ValueError(f"Missing required fields: {', '.join(missing_fields)}")
    
    # Build contact person name
    contact_person = None
    first_name = safe_string_convert(row.get('CONTACT FIRST NAME'))
    last_name = safe_string_convert(row.get('CONTACT LAST NAME'))
    if first_name or last_name:
        contact_person = f"{first_name or ''} {last_name or ''}".strip()
        if not contact_person:
            contact_person = None
    
    # Build notes field
    notes_parts = []
    sic_desc = safe_string_convert(row.get('SIC_DESC'))
    owner_name = safe_string_convert(row.get('OWNER_NAME'))
    
    if sic_desc:
        notes_parts.append(f"Industry: {sic_desc}")
    if owner_name:
        notes_parts.append(f"Owner: {owner_name}")
    
    notes = "\n".join(notes_parts) if notes_parts else None
    
    # Clean and validate data
    return {
        'name': safe_string_convert(row['PLANT_NAME'], 100),
        'contact_person': safe_string_convert(contact_person, 100),
        'contact_title': safe_string_convert(row.get('CONTACT TITLE'), 100),
        'email': validate_email(row.get('CONTACT EMAIL')),
        'phone': clean_phone_number(safe_string_convert(row.get('PHONE'))),
        'phone_label': 'work',
        'address': safe_string_convert(row.get('ADDRESS'), 255),
        'city': safe_string_convert(row.get('CITY'), 100),
        'state': safe_string_convert(row.get('STATE'), 100),
        'zip': safe_string_convert(row.get('ZIP')),  # Add ZIP if available
        'notes': notes,
        'type': 'Food and Beverage',
        'lead_status': 'open'
    }