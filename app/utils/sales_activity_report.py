"""Actor-based reporting with database-side filtering, grouping and pagination."""
from datetime import datetime, timedelta
from sqlalchemy import select, union_all, literal, exists, func, case, and_, cast, String
from app.models import Lead, Client, Project, Interaction, ActivityLog, ActivityType, User, Subscription, File


def date_bounds(start, end):
    def parse(value):
        if not value:
            return None
        parsed = datetime.strptime(value, "%Y-%m-%d")
        if parsed.strftime("%Y-%m-%d") != value:
            raise ValueError("Use YYYY-MM-DD dates")
        return parsed
    lower, upper = parse(start), parse(end)
    if lower and upper and lower > upper:
        raise ValueError("Start date must be on or before end date")
    return lower, upper + timedelta(days=1) if upper else None


def build_report(session, tenant_id, start=None, end=None, user_id=None, page=1, per_page=50):
    lower, upper = date_bounds(start, end)
    logs = ActivityLog
    parts = [select(
        literal("log").label("source"), logs.id.label("event_id"), logs.user_id,
        logs.timestamp.label("occurred_at"), cast(logs.action, String).label("action"),
        logs.entity_type, logs.entity_id, logs.description.label("record_name"),
    ).where(logs.tenant_id == tenant_id)]
    # Existing rows provide reliable historical creation attribution. Logged creations
    # take precedence, including after a record is permanently deleted.
    for model, actor, time, name in (
        (Lead, Lead.created_by, Lead.created_at, Lead.name),
        (Client, Client.created_by, Client.created_at, Client.name),
        (Project, Project.created_by, Project.created_at, Project.project_name),
        (Subscription, Subscription.created_by, Subscription.created_at, Subscription.plan_name),
        (File, File.user_id, File.uploaded_at, File.filename),
    ):
        kind = model.__name__.lower()
        logged = exists(select(logs.id).where(
            logs.tenant_id == tenant_id, logs.entity_type == kind,
            logs.entity_id == model.id, logs.action == ActivityType.created,
        ))
        parts.append(select(literal("record"), model.id, actor, time,
                            literal("created"), literal(kind), model.id, name).where(
            model.tenant_id == tenant_id, ~logged))
        if model in (Lead, Client, Project):
            updater = model.last_updated_by if model is Project else model.updated_by
            # This is a snapshot of the latest old edit, not a fabricated edit history.
            logged_edit = exists(select(logs.id).where(
                logs.tenant_id == tenant_id, logs.entity_type == kind,
                logs.entity_id == model.id, logs.action == ActivityType.edited,
            ))
            parts.append(select(literal("latest_edit"), model.id, updater, model.updated_at,
                                literal("edited"), literal(kind), model.id, name).where(
                model.tenant_id == tenant_id, updater.isnot(None), model.updated_at.isnot(None), ~logged_edit))
            logged_delete = exists(select(logs.id).where(
                logs.tenant_id == tenant_id, logs.entity_type == kind,
                logs.entity_id == model.id, logs.action == ActivityType.deleted))
            parts.append(select(literal("legacy_delete"), model.id, model.deleted_by, model.deleted_at,
                                literal("deleted"), literal(kind), model.id, name).where(
                model.tenant_id == tenant_id, model.deleted_by.isnot(None),
                model.deleted_at.isnot(None), ~logged_delete))
    # Legacy interactions have no author. Keep them visible without giving credit
    # to the current owner of their parent record.
    parts.append(select(literal("legacy_interaction"), Interaction.id, literal(None),
                        Interaction.contact_date, literal("created"), literal("interaction"),
                        Interaction.id, Interaction.summary).where(
        Interaction.tenant_id == tenant_id,
        ~exists(select(logs.id).where(logs.tenant_id == tenant_id,
            logs.entity_type == "interaction", logs.entity_id == Interaction.id,
            logs.action == ActivityType.created))))
    events = union_all(*parts).subquery()
    filters = []
    if lower:
        filters.append(events.c.occurred_at >= lower)
    if upper:
        filters.append(events.c.occurred_at < upper)
    if user_id is not None:
        filters.append(events.c.user_id == user_id)
    selected = select(events).where(*filters).subquery()
    def count(action, kind=None):
        condition = selected.c.action == action
        if kind:
            condition = and_(condition, selected.c.entity_type == kind)
        return func.sum(case((condition, 1), else_=0))
    groups = session.execute(select(selected.c.user_id,
        count("created", "lead"), count("created", "client"), count("created", "project"),
        count("created", "interaction"), count("edited"), count("deleted"), count("viewed"),
        func.count(),
    ).group_by(selected.c.user_id)).all()
    totals = {row[0]: row[1:] for row in groups}
    users = session.query(User).filter(User.tenant_id == tenant_id).order_by(User.email).all()
    emails = {u.id: u.email for u in users}
    keys = ("leads_created", "clients_created", "projects_created", "interactions", "edits", "deletions", "views", "total")
    summary = [dict(user_id=u.id, email=u.email, is_active=u.is_active,
                    **dict(zip(keys, totals.get(u.id, (0,) * len(keys)))))
               for u in users if user_id is None or user_id == u.id]
    total = session.scalar(select(func.count()).select_from(selected))
    rows = session.execute(select(selected).order_by(selected.c.occurred_at.desc(),
        selected.c.source, selected.c.event_id.desc()).offset((page - 1) * per_page).limit(per_page)).mappings()
    details = []
    for row in rows:
        item = dict(row)
        action = item["action"]
        item["action"] = action.value if isinstance(action, ActivityType) else action
        item["occurred_at"] = item["occurred_at"].isoformat() + "Z" if item["occurred_at"] else None
        item["email"] = emails.get(item["user_id"], "Author not recorded")
        details.append(item)
    return dict(users=summary, events=details, total=total, page=page, per_page=per_page,
                unattributed_total=totals.get(None, (0,) * len(keys))[-1], timezone="UTC")
