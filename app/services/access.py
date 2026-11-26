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


def my_clients_filter(principal: Principal):
    """The web Clients page: clients assigned to the user, plus unassigned
    clients they entered. The same for every role, admins included."""
    from app.models import Client

    return and_(
        Client.tenant_id == principal.tenant_id,
        Client.deleted_at.is_(None),
        or_(
            Client.assigned_to == principal.user_id,
            and_(Client.assigned_to.is_(None), Client.created_by == principal.user_id),
        ),
    )


def my_projects_filter(principal: Principal):
    """The web Projects page, for every role, admins included: projects
    assigned to the user; unassigned projects whose client or lead is on the
    user's own Clients or Leads page (assigned to them, or unassigned and
    entered by them); and unassigned projects with no parent they entered.
    Callers also apply project_access_filter."""
    from app.models import Project, Client, Lead

    def mine(model):
        return or_(
            model.assigned_to == principal.user_id,
            and_(model.assigned_to.is_(None), model.created_by == principal.user_id),
        )

    return and_(
        Project.tenant_id == principal.tenant_id,
        Project.deleted_at.is_(None),
        or_(
            Project.assigned_to == principal.user_id,
            and_(
                Project.assigned_to.is_(None),
                or_(
                    and_(Project.client_id.isnot(None), Project.client.has(mine(Client))),
                    and_(Project.lead_id.isnot(None), Project.lead.has(mine(Lead))),
                    and_(
                        Project.client_id.is_(None),
                        Project.lead_id.is_(None),
                        Project.created_by == principal.user_id,
                    ),
                ),
            ),
        ),
    )


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
