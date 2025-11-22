import re
from typing import Optional

def clean_phone_number(phone: str) -> Optional[str]:
    """
    Clean and standardize phone number for storage.
    Stores in E.164 format: +1XXXXXXXXXX for US numbers
    
    Args:
        phone: Raw phone input from user
        
    Returns:
        Cleaned phone number or None if invalid
    """
    if not phone or not isinstance(phone, str):
        return None
    
    # Remove all non-numeric characters
    cleaned = re.sub(r'[^\d]', '', phone.strip())
    
    if not cleaned:
        return None
    
    # Handle different US phone number formats
    if len(cleaned) == 10:
        # Standard US 10-digit number
        return f"+1{cleaned}"
    elif len(cleaned) == 11 and cleaned.startswith('1'):
        # 11-digit starting with 1
        return f"+{cleaned}"
    elif len(cleaned) == 7:
        # 7-digit local number - return as-is for now
        # You might want to add area code logic here
        return cleaned
    
    # For international numbers or other formats, return as-is if reasonable length
    if 7 <= len(cleaned) <= 15:
        return f"+{cleaned}"
    
    return None

def format_phone_display(phone: str) -> str:
    """
    Format phone number for display in UI.
    
    Args:
        phone: Stored phone number (should be in +1XXXXXXXXXX format)
        
    Returns:
        Formatted phone number for display
    """
    if not phone:
        return ""
    
    # Remove + and any non-digits
    cleaned = re.sub(r'[^\d]', '', phone)
    
    if len(cleaned) == 11 and cleaned.startswith('1'):
        # US number: +1 (XXX) XXX-XXXX
        return f"({cleaned[1:4]}) {cleaned[4:7]}-{cleaned[7:]}"
    elif len(cleaned) == 10:
        # US number without country code: (XXX) XXX-XXXX
        return f"({cleaned[:3]}) {cleaned[3:6]}-{cleaned[6:]}"
    elif len(cleaned) == 7:
        # Local number: XXX-XXXX
        return f"{cleaned[:3]}-{cleaned[3:]}"
    
    # For other formats, return the original
    return phone

def validate_phone_number(phone: str) -> bool:
    """
    Validate if phone number is in acceptable format.
    
    Args:
        phone: Phone number to validate
        
    Returns:
        True if valid, False otherwise
    """
    if not phone:
        return False
    
    cleaned = re.sub(r'[^\d]', '', phone)
    
    # Accept US phone numbers (10 or 11 digits) or international (7-15 digits)
    if len(cleaned) == 10:
        return True
    elif len(cleaned) == 11 and cleaned.startswith('1'):
        return True
    elif 7 <= len(cleaned) <= 15:
        return True
    
    return False