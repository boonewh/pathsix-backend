"""SQL permissions shared by tenant-bound record services."""

from sqlalchemy import and_, or_
from app.services.principal import Principal


def owned_record_filter(model, principal: Principal, *, include_deleted=False):
    predicates = [model.tenant_id == principal.tenant_id]
    if not include_deleted:
        predicates.append(model.deleted_at.is_(None))
    if not principal.is_admin:
        predicates.append(
            or_(
                model.created_by == principal.user_id,
                model.assigned_to == principal.user_id,
            )
        )
    return and_(*predicates)


def project_access_filter(principal):
    from app.models import Project, Client, Lead

    # Validate parent tenant even for an admin or directly assigned project.
    valid_parent = or_(
        and_(Project.client_id.is_(None), Project.lead_id.is_(None)),
        and_(
            Project.lead_id.is_(None),
            Project.client.has(
                and_(
                    Client.tenant_id == principal.tenant_id, Client.deleted_at.is_(None)
                )
            ),
        ),
        and_(
            Project.client_id.is_(None),
            Project.lead.has(
                and_(Lead.tenant_id == principal.tenant_id, Lead.deleted_at.is_(None))
            ),
        ),
    )
    if principal.is_admin:
        return valid_parent
    inherited = or_(
        Project.client.has(owned_record_filter(Client, principal)),
        Project.lead.has(owned_record_filter(Lead, principal)),
        and_(
            Project.client_id.is_(None),
            Project.lead_id.is_(None),
            Project.created_by == principal.user_id,
        ),
    )
    return and_(
        valid_parent,
        or_(
            Project.assigned_to == principal.user_id,
            and_(Project.assigned_to.is_(None), inherited),
        ),
    )


def interaction_access_filter(principal):
    from app.models import Interaction, Client, Lead, Project

    return or_(
        and_(
            Interaction.lead_id.is_(None),
            Interaction.project_id.is_(None),
            Interaction.client.has(owned_record_filter(Client, principal)),
        ),
        and_(
            Interaction.client_id.is_(None),
            Interaction.project_id.is_(None),
            Interaction.lead.has(owned_record_filter(Lead, principal)),
        ),
        and_(
            Interaction.client_id.is_(None),
            Interaction.lead_id.is_(None),
            Interaction.project.has(
                and_(
                    Project.tenant_id == principal.tenant_id,
                    Project.deleted_at.is_(None),
                    project_access_filter(principal),
                )
            ),
        ),
    )
