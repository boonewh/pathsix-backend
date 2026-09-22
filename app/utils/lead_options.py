"""Tenant option normalization, without rejecting historical or custom values."""

from app.models import Tenant


def tenant_lead_config(session, tenant_id):
    config = session.query(Tenant.config).filter(Tenant.id == tenant_id).scalar()
    return config if isinstance(config, dict) else {}


def configured_options(value):
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def normalize_option(value, options):
    if value is None:
        return None
    value = value.strip()
    return next((option for option in options if option.casefold() == value.casefold()), value)


def normalize_lead_options(values, config, *, creating=False):
    """Return supplied fields only on update; defaults apply only to creation.

    Unknown values remain supported. Matching ignores case and surrounding
    whitespace, but never guesses that different status names mean the same thing.
    """
    values = dict(values)
    leads = config.get('leads') or {}
    statuses = configured_options(leads.get('statuses')) if isinstance(leads, dict) else []
    types = configured_options(config.get('businessTypes'))
    for field, options, default in (
        ('lead_status', statuses, statuses[0] if statuses else 'open'),
        ('type', types, 'None'),
    ):
        if creating and (values.get(field) is None or not values[field].strip()):
            values[field] = default
        if field in values:
            values[field] = normalize_option(values[field], options)
    return values
