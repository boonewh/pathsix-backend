"""Admin reporting with explicit tenant context and caller-owned sessions.

Read methods do not commit or record views. Adapters authenticate a fresh Principal
and parse HTTP parameters; this service authorizes access without HTTP globals.
"""
from datetime import datetime, timedelta, timezone
from dateutil.parser import parse as parse_date
from sqlalchemy import func, and_, or_, case, distinct
from sqlalchemy.orm import with_loader_criteria
from app.models import Lead, Project, Client, Interaction, User, ActivityLog, Subscription
from app.services.base import TenantService
from app.utils.sales_activity_report import build_report


class ReportService(TenantService):
    def __init__(self, session, principal):
        super().__init__(session, principal)
        self._require_admin()

    def _query(self, *entities):
        # Scope the root in WHERE and joined models in their JOIN conditions.
        # Filtering every selected model in WHERE would turn outer joins into
        # inner joins, dropping client-only or lead-only interaction rows.
        root = self.session.query(*entities).column_descriptions[0]["entity"]
        tenant_id = self.principal.tenant_id
        # Historical reports may include deleted parents, but never malformed or
        # cross-tenant links. Keep these predicates on joins as well as root reads.
        valid_project = or_(
            and_(Project.client_id.is_(None), Project.lead_id.is_(None)),
            and_(Project.lead_id.is_(None), Project.client.has(Client.tenant_id == tenant_id)),
            and_(Project.client_id.is_(None), Project.lead.has(Lead.tenant_id == tenant_id)),
        )
        valid_interaction = or_(
            and_(Interaction.lead_id.is_(None), Interaction.project_id.is_(None),
                 Interaction.client.has(Client.tenant_id == tenant_id)),
            and_(Interaction.client_id.is_(None), Interaction.project_id.is_(None),
                 Interaction.lead.has(Lead.tenant_id == tenant_id)),
            and_(Interaction.client_id.is_(None), Interaction.lead_id.is_(None),
                 Interaction.project.has(and_(Project.tenant_id == tenant_id, valid_project))),
        )
        return super()._query(root).with_entities(*entities).options(
            with_loader_criteria(Project, valid_project, include_aliases=True),
            with_loader_criteria(Interaction, valid_interaction, include_aliases=True),
            with_loader_criteria(Subscription, Subscription.client.has(Client.tenant_id == tenant_id),
                                 include_aliases=True),
        )

    @staticmethod
    def _date_range(start, end):
        def parse(value):
            if value is None or value == "":
                return None
            if not isinstance(value, str):
                raise ValueError("Invalid date")
            parsed = parse_date(value)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        lower, upper = parse(start), parse(end)
        if lower and upper and lower > upper:
            raise ValueError("Start date must be on or before end date")
        return lower, upper

    @staticmethod
    def _bounded_integer(value, name, lower=1, upper=3650):
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError(f"Invalid {name}")

    def sales_activity_report(self, start_date=None, end_date=None, user_id=None, page=1):
        self._pagination(page, 50)
        if user_id is not None:
            self._bounded_integer(user_id, "user", upper=2147483647)
        return build_report(self.session, self.principal.tenant_id, start_date, end_date, user_id, page)

    def get_reports(self, start_date=None, end_date=None):
        start_date, end_date = self._date_range(start_date, end_date)
        tenant_id = self.principal.tenant_id
        filters = [Lead.tenant_id == tenant_id, Lead.deleted_at == None]
        project_filters = [Project.tenant_id == tenant_id]
        if start_date:
            dt_start = start_date
            filters.append(Lead.created_at >= dt_start)
            project_filters.append(Project.created_at >= dt_start)
        if end_date:
            dt_end = end_date
            filters.append(Lead.created_at <= dt_end)
            project_filters.append(Project.created_at <= dt_end)
        total_leads = self._query(func.count(Lead.id)).filter(*filters).scalar()
        converted_leads = self._query(func.count(Lead.id)).filter(
            *filters,
            Lead.lead_status == "won"
        ).scalar()
        total_projects = self._query(func.count(Project.id)).filter(*project_filters).scalar()
        won_projects = self._query(func.count(Project.id)).filter(
            *project_filters,
            Project.project_status == "completed"
        ).scalar()
        lost_projects = self._query(func.count(Project.id)).filter(
            *project_filters,
            Project.project_status == "lost"
        ).scalar()
        total_won_value = self._query(func.coalesce(func.sum(Project.project_worth), 0)).filter(
            *project_filters,
            Project.project_status == "completed"
        ).scalar()
        return {
            "lead_count": total_leads,
            "converted_leads": converted_leads,
            "project_count": total_projects,
            "won_projects": won_projects,
            "lost_projects": lost_projects,
            "total_won_value": total_won_value
        }

    def sales_pipeline(self, start_date=None, end_date=None, user_filter=None):
        start_date, end_date = self._date_range(start_date, end_date)
        if user_filter is not None:
            self._bounded_integer(user_filter, "user", upper=2147483647)
        tenant_id = self.principal.tenant_id
        lead_filters = [Lead.tenant_id == tenant_id, Lead.deleted_at == None]
        if start_date:
            lead_filters.append(Lead.created_at >= start_date)
        if end_date:
            lead_filters.append(Lead.created_at <= end_date)
        if user_filter:
            tenant_users = self._query(User.id).filter(User.id == user_filter)
            lead_filters.append(Lead.assigned_to.in_(tenant_users))
        lead_pipeline = self._query(
            Lead.lead_status,
            func.count(Lead.id).label('count')
        ).filter(*lead_filters).group_by(Lead.lead_status).all()
        project_filters = [Project.tenant_id == tenant_id, Project.deleted_at == None]
        if start_date:
            project_filters.append(Project.created_at >= start_date)
        if end_date:
            project_filters.append(Project.created_at <= end_date)
        project_pipeline = self._query(
            Project.project_status,
            func.count(Project.id).label('count'),
            func.coalesce(func.sum(Project.project_worth), 0).label('total_value')
        ).filter(*project_filters).group_by(Project.project_status).all()
        project_vtype = self._query(
            Project.project_status,
            Project.value_type,
            func.count(Project.id).label('count'),
            func.coalesce(func.sum(Project.project_worth), 0).label('total_value')
        ).filter(*project_filters).group_by(Project.project_status, Project.value_type).all()
        vtype_by_status = {}
        for row in project_vtype:
            status = row.project_status
            if status not in vtype_by_status:
                vtype_by_status[status] = {}
            vt = row.value_type or 'one_time'
            vtype_by_status[status][vt] = {
                'count': row.count,
                'total_value': float(row.total_value)
            }
        return {
            "leads": [{"status": row.lead_status, "count": row.count} for row in lead_pipeline],
            "projects": [{
                "status": row.project_status,
                "count": row.count,
                "total_value": float(row.total_value),
                "by_value_type": vtype_by_status.get(row.project_status, {})
            } for row in project_pipeline]
        }

    def lead_source_report(self, start_date=None, end_date=None):
        start_date, end_date = self._date_range(start_date, end_date)
        tenant_id = self.principal.tenant_id
        filters = [Lead.tenant_id == tenant_id, or_(Lead.deleted_at == None, Lead.lead_status == 'won')]
        if start_date:
            filters.append(Lead.created_at >= start_date)
        if end_date:
            filters.append(Lead.created_at <= end_date)
        results = self._query(
            Lead.lead_source,
            func.count(Lead.id).label('total_leads'),
            func.sum(case((Lead.lead_status == 'won', 1), else_=0)).label('converted'),
            func.sum(case((Lead.lead_status == 'qualified', 1), else_=0)).label('qualified')
        ).filter(*filters).group_by(Lead.lead_source).all()
        return {
            "sources": [{
                "source": row.lead_source or "Unknown",
                "total_leads": row.total_leads,
                "converted": row.converted,
                "qualified": row.qualified,
                "conversion_rate": round((row.converted / row.total_leads * 100), 2) if row.total_leads > 0 else 0
            } for row in results]
        }

    def conversion_rate_report(self, start_date=None, end_date=None):
        start_date, end_date = self._date_range(start_date, end_date)
        tenant_id = self.principal.tenant_id
        filters = [Lead.tenant_id == tenant_id, or_(Lead.deleted_at == None, Lead.lead_status == 'won')]
        if start_date:
            filters.append(Lead.created_at >= start_date)
        if end_date:
            filters.append(Lead.created_at <= end_date)
        total_leads = self._query(func.count(Lead.id)).filter(*filters).scalar()
        converted_leads = self._query(func.count(Lead.id)).filter(*filters, Lead.lead_status == 'won').scalar()
        overall_rate = round((converted_leads / total_leads * 100), 2) if total_leads > 0 else 0
        by_user = []
        user_stats = self._query(
            Lead.assigned_to, User.email,
            func.count(Lead.id).label('total'),
            func.sum(case((Lead.lead_status == 'won', 1), else_=0)).label('converted')
        ).join(User, Lead.assigned_to == User.id).filter(*filters).group_by(Lead.assigned_to, User.email).all()

        by_user = [{
            "user_id": row.assigned_to,
            "user_email": row.email,
            "total_leads": row.total,
            "converted": row.converted,
            "conversion_rate": round((row.converted / row.total * 100), 2) if row.total > 0 else 0
        } for row in user_stats]

        # Include unassigned leads so totals add up
        unassigned_total = self._query(func.count(Lead.id)).filter(
            *filters, Lead.assigned_to == None
        ).scalar() or 0
        unassigned_converted = self._query(func.count(Lead.id)).filter(
            *filters, Lead.assigned_to == None, Lead.lead_status == 'won'
        ).scalar() or 0
        if unassigned_total > 0:
            by_user.append({
                "user_id": None,
                "user_email": "Unassigned",
                "total_leads": unassigned_total,
                "converted": unassigned_converted,
                "conversion_rate": round((unassigned_converted / unassigned_total * 100), 2) if unassigned_total > 0 else 0
            })

        if self.session.get_bind().dialect.name == 'postgresql':
            # PostgreSQL approach: EXTRACT(EPOCH FROM (converted_on - created_at)) / 86400
            avg_days = self._query(
                func.avg(
                    func.extract('epoch', Lead.converted_on - Lead.created_at) / 86400
                )
            ).filter(*filters, Lead.lead_status == 'won', Lead.converted_on != None).scalar()
        else:
            # SQLite approach: julianday
            avg_days = self._query(
                func.avg(func.julianday(Lead.converted_on) - func.julianday(Lead.created_at))
            ).filter(*filters, Lead.lead_status == 'won', Lead.converted_on != None).scalar()
        return {
            "overall": {
                "total_leads": total_leads,
                "converted_leads": converted_leads,
                "conversion_rate": overall_rate,
                "avg_days_to_convert": round(avg_days, 1) if avg_days else None
            },
            "by_user": by_user
        }

    def revenue_by_client(self, start_date=None, end_date=None, limit=50):
        start_date, end_date = self._date_range(start_date, end_date)
        self._bounded_integer(limit, "limit", upper=200)
        tenant_id = self.principal.tenant_id
        filters = [Project.tenant_id == tenant_id, Project.deleted_at == None]
        if start_date:
            filters.append(Project.created_at >= start_date)
        if end_date:
            filters.append(Project.created_at <= end_date)
        client_revenue = self._query(
            Client.id, Client.name,
            func.count(Project.id).label('project_count'),
            func.coalesce(func.sum(case((Project.project_status == 'completed', Project.project_worth), else_=0)), 0).label('won_value'),
            func.coalesce(func.sum(case((Project.project_status == 'active', Project.project_worth), else_=0)), 0).label('pending_value'),
            func.coalesce(func.sum(Project.project_worth), 0).label('total_value')
        ).join(Project, Client.id == Project.client_id).filter(*filters).group_by(
            Client.id, Client.name
        ).order_by(func.sum(Project.project_worth).desc()).limit(limit).all()
        client_ids = [row.id for row in client_revenue]
        vtype_rows = self._query(
            Project.client_id,
            Project.value_type,
            func.coalesce(func.sum(case((Project.project_status == 'completed', Project.project_worth), else_=0)), 0).label('won_value'),
        ).filter(*filters, Project.client_id.in_(client_ids)).group_by(
            Project.client_id, Project.value_type
        ).all()
        vtype_map = {}
        for row in vtype_rows:
            cid = row.client_id
            if cid not in vtype_map:
                vtype_map[cid] = {}
            vt = row.value_type or 'one_time'
            vtype_map[cid][vt] = float(row.won_value)
        def calc_mrr(breakdown):
            monthly = breakdown.get('monthly', 0)
            yearly = breakdown.get('yearly', 0)
            return round(monthly + yearly / 12, 2)
        return {
            "clients": [{
                "client_id": row.id,
                "client_name": row.name,
                "project_count": row.project_count,
                "won_value": float(row.won_value),
                "pending_value": float(row.pending_value),
                "total_value": float(row.total_value),
                "value_type_breakdown": vtype_map.get(row.id, {}),
                "mrr": calc_mrr(vtype_map.get(row.id, {})),
            } for row in client_revenue]
        }

    def user_activity_report(self, start_date=None, end_date=None):
        start_date, end_date = self._date_range(start_date, end_date)
        tenant_id = self.principal.tenant_id
        date_filter = []
        if start_date:
            date_filter.append(Interaction.contact_date >= start_date)
        if end_date:
            date_filter.append(Interaction.contact_date <= end_date)
        users = self._query(User).filter(User.tenant_id == tenant_id, User.is_active == True).all()
        user_stats = []
        for u in users:
            interaction_count = self._query(func.count(Interaction.id)).filter(
                Interaction.tenant_id == tenant_id,
                or_(
                    Interaction.client_id.in_(self._query(Client.id).filter(Client.assigned_to == u.id, Client.tenant_id == tenant_id)),
                    Interaction.lead_id.in_(self._query(Lead.id).filter(Lead.assigned_to == u.id, Lead.tenant_id == tenant_id))
                ),
                *date_filter
            ).scalar()

            leads_assigned = self._query(func.count(Lead.id)).filter(
                Lead.tenant_id == tenant_id, Lead.assigned_to == u.id, Lead.deleted_at == None
            ).scalar()

            clients_assigned = self._query(func.count(Client.id)).filter(
                Client.tenant_id == tenant_id, Client.assigned_to == u.id, Client.deleted_at == None
            ).scalar()

            activity_count = self._query(func.count(ActivityLog.id)).filter(
                ActivityLog.tenant_id == tenant_id,
                ActivityLog.user_id == u.id,
                *([ActivityLog.timestamp >= start_date] if start_date else []),
                *([ActivityLog.timestamp <= end_date] if end_date else [])
            ).scalar()

            user_stats.append({
                "user_id": u.id,
                "email": u.email,
                "interactions": interaction_count,
                "leads_assigned": leads_assigned,
                "clients_assigned": clients_assigned,
                "activity_count": activity_count
            })
        return {"users": user_stats}

    def follow_up_report(self, days_threshold=30):
        self._bounded_integer(days_threshold, "days", lower=0)
        tenant_id = self.principal.tenant_id
        now = datetime.utcnow()
        overdue_filters = [
            Interaction.tenant_id == tenant_id,
            Interaction.follow_up != None,
            Interaction.follow_up < now,
            Interaction.followup_status.in_(['pending', 'rescheduled'])
        ]
        overdue = self._query(
            Interaction.id, Interaction.client_id, Interaction.lead_id,
            Interaction.follow_up, Interaction.summary,
            Client.name.label('client_name'), Lead.name.label('lead_name')
        ).outerjoin(Client, Interaction.client_id == Client.id).outerjoin(
            Lead, Interaction.lead_id == Lead.id
        ).filter(*overdue_filters).order_by(Interaction.follow_up.asc()).all()
        inactive_threshold = now - timedelta(days=days_threshold)
        recent_client_interactions = self._query(distinct(Interaction.client_id)).filter(
            Interaction.tenant_id == tenant_id,
            Interaction.client_id != None,
            Interaction.contact_date >= inactive_threshold
        ).subquery()
        inactive_clients = self._query(
            Client.id, Client.name,
            func.max(Interaction.contact_date).label('last_interaction')
        ).outerjoin(Interaction, Client.id == Interaction.client_id).filter(
            Client.tenant_id == tenant_id,
            Client.deleted_at == None,
            ~Client.id.in_(recent_client_interactions)
        ).group_by(Client.id, Client.name).all()
        recent_lead_interactions = self._query(distinct(Interaction.lead_id)).filter(
            Interaction.tenant_id == tenant_id,
            Interaction.lead_id != None,
            Interaction.contact_date >= inactive_threshold
        ).subquery()
        inactive_leads = self._query(
            Lead.id, Lead.name,
            func.max(Interaction.contact_date).label('last_interaction')
        ).outerjoin(Interaction, Lead.id == Interaction.lead_id).filter(
            Lead.tenant_id == tenant_id,
            Lead.deleted_at == None,
            Lead.lead_status.in_(['open', 'qualified', 'proposal']),
            ~Lead.id.in_(recent_lead_interactions)
        ).group_by(Lead.id, Lead.name).all()
        return {
            "overdue_follow_ups": [{
                "interaction_id": row.id,
                "client_id": row.client_id,
                "lead_id": row.lead_id,
                "entity_name": row.client_name or row.lead_name,
                "follow_up_date": row.follow_up.isoformat() if row.follow_up else None,
                "summary": row.summary,
                "days_overdue": (now - row.follow_up).days if row.follow_up else 0
            } for row in overdue],
            "inactive_clients": [{
                "client_id": row.id,
                "name": row.name,
                "last_interaction": row.last_interaction.isoformat() if row.last_interaction else None,
                "days_inactive": (now - row.last_interaction).days if row.last_interaction else None
            } for row in inactive_clients],
            "inactive_leads": [{
                "lead_id": row.id,
                "name": row.name,
                "last_interaction": row.last_interaction.isoformat() if row.last_interaction else None,
                "days_inactive": (now - row.last_interaction).days if row.last_interaction else None
            } for row in inactive_leads]
        }

    def client_retention_report(self, start_date=None, end_date=None):
        start_date, end_date = self._date_range(start_date, end_date)
        tenant_id = self.principal.tenant_id
        filters = [Client.tenant_id == tenant_id]
        if start_date:
            filters.append(Client.created_at >= start_date)
        if end_date:
            filters.append(Client.created_at <= end_date)
        status_breakdown = self._query(
            Client.status, func.count(Client.id).label('count')
        ).filter(*filters, Client.deleted_at == None).group_by(Client.status).all()
        churned_count = self._query(func.count(Client.id)).filter(
            Client.tenant_id == tenant_id,
            Client.deleted_at != None,
            *([Client.deleted_at >= start_date] if start_date else []),
            *([Client.deleted_at <= end_date] if end_date else [])
        ).scalar()
        total_active = self._query(func.count(Client.id)).filter(*filters, Client.deleted_at == None).scalar()
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        active_with_interactions = self._query(func.count(distinct(Interaction.client_id))).filter(
            Interaction.tenant_id == tenant_id,
            Interaction.client_id != None,
            Interaction.contact_date >= thirty_days_ago
        ).scalar()
        return {
            "status_breakdown": [{"status": row.status, "count": row.count} for row in status_breakdown],
            "total_active": total_active,
            "churned": churned_count,
            "active_with_recent_interactions": active_with_interactions,
            "retention_rate": round((total_active / (total_active + churned_count) * 100), 2) if (total_active + churned_count) > 0 else 0
        }

    def project_performance_report(self, start_date=None, end_date=None):
        start_date, end_date = self._date_range(start_date, end_date)
        tenant_id = self.principal.tenant_id
        filters = [Project.tenant_id == tenant_id, Project.deleted_at == None]
        if start_date:
            filters.append(Project.created_at >= start_date)
        if end_date:
            filters.append(Project.created_at <= end_date)
        status_counts = self._query(
            Project.project_status,
            func.count(Project.id).label('count'),
            func.coalesce(func.sum(Project.project_worth), 0).label('total_value')
        ).filter(*filters).group_by(Project.project_status).all()
        total_projects = self._query(func.count(Project.id)).filter(*filters).scalar()
        won_projects = self._query(func.count(Project.id)).filter(*filters, Project.project_status == 'completed').scalar()
        win_rate = round((won_projects / total_projects * 100), 2) if total_projects > 0 else 0

        if self.session.get_bind().dialect.name == 'postgresql':
            # PostgreSQL approach: EXTRACT(EPOCH FROM (project_end - project_start)) / 86400
            avg_duration = self._query(
                func.avg(
                    func.extract('epoch', Project.project_end - Project.project_start) / 86400
                )
            ).filter(*filters, Project.project_start != None, Project.project_end != None, Project.project_status == 'completed').scalar()
        else:
            # SQLite approach: julianday
            avg_duration = self._query(
                func.avg(func.julianday(Project.project_end) - func.julianday(Project.project_start))
            ).filter(*filters, Project.project_start != None, Project.project_end != None, Project.project_status == 'completed').scalar()
        avg_value = self._query(func.avg(Project.project_worth)).filter(*filters, Project.project_worth != None).scalar()
        return {
            "status_breakdown": [{
                "status": row.project_status,
                "count": row.count,
                "total_value": float(row.total_value)
            } for row in status_counts],
            "total_projects": total_projects,
            "win_rate": win_rate,
            "avg_duration_days": round(avg_duration, 1) if avg_duration else None,
            "avg_project_value": round(float(avg_value), 2) if avg_value else None
        }

    def upcoming_tasks_report(self, days_ahead=30, user_filter=None):
        if user_filter is not None:
            self._bounded_integer(user_filter, "user", upper=2147483647)
        self._bounded_integer(days_ahead, "days", lower=0)
        tenant_id = self.principal.tenant_id
        now = datetime.utcnow()
        future_date = now + timedelta(days=days_ahead)
        filters = [
            Interaction.tenant_id == tenant_id,
            Interaction.follow_up != None,
            Interaction.follow_up >= now,
            Interaction.follow_up <= future_date,
            Interaction.followup_status.in_(['pending', 'rescheduled'])
        ]
        if user_filter:
            tenant_users = self._query(User.id).filter(User.id == user_filter)
            filters.extend([or_(
                Interaction.client_id.in_(self._query(Client.id).filter(Client.assigned_to.in_(tenant_users))),
                Interaction.lead_id.in_(self._query(Lead.id).filter(Lead.assigned_to.in_(tenant_users)))
            )])
        upcoming = self._query(
            Interaction.id, Interaction.client_id, Interaction.lead_id,
            Interaction.follow_up, Interaction.summary, Interaction.followup_status,
            Client.name.label('client_name'), Lead.name.label('lead_name'),
            Client.assigned_to.label('client_assigned_to'), Lead.assigned_to.label('lead_assigned_to')
        ).outerjoin(Client, Interaction.client_id == Client.id).outerjoin(
            Lead, Interaction.lead_id == Lead.id
        ).filter(*filters).order_by(Interaction.follow_up.asc()).all()
        assigned_user_ids = set()
        for row in upcoming:
            if row.client_assigned_to:
                assigned_user_ids.add(row.client_assigned_to)
            if row.lead_assigned_to:
                assigned_user_ids.add(row.lead_assigned_to)
        user_map = {}
        if assigned_user_ids:
            users = self._query(User.id, User.email).filter(User.id.in_(assigned_user_ids)).all()
            user_map = {u.id: u.email for u in users}
        return {
            "upcoming_tasks": [{
                "interaction_id": row.id,
                "client_id": row.client_id,
                "lead_id": row.lead_id,
                "entity_name": row.client_name or row.lead_name,
                "follow_up_date": row.follow_up.isoformat() if row.follow_up else None,
                "summary": row.summary,
                "status": row.followup_status.value if row.followup_status else None,
                "days_until": (row.follow_up - now).days if row.follow_up else None,
                "assigned_to": user_map.get(row.client_assigned_to or row.lead_assigned_to)
            } for row in upcoming]
        }

    def revenue_forecast_report(self):
        tenant_id = self.principal.tenant_id
        WEIGHTS = {'active': 0.3, 'completed': 1.0, 'lost': 0.0}
        projects = self._query(
            Project.project_status, Project.project_worth, Project.value_type
        ).filter(
            Project.tenant_id == tenant_id,
            Project.deleted_at == None,
            Project.project_worth != None
        ).all()
        forecast_by_status = {}
        total_forecast = 0
        total_mrr = 0.0
        total_arr = 0.0
        for project in projects:
            status = project.project_status
            worth = float(project.project_worth or 0)
            value_type = project.value_type or 'one_time'
            weight = WEIGHTS.get(status, 0)

            # Annualize for forecast: monthly recurring × 12, yearly and one-time as-is
            annualized = worth * 12 if value_type == 'monthly' else worth
            weighted_value = annualized * weight

            if status not in forecast_by_status:
                forecast_by_status[status] = {
                    'count': 0,
                    'total_value': 0,
                    'annualized_value': 0,
                    'weighted_value': 0,
                    'weight': weight,
                    'by_type': {}
                }

            entry = forecast_by_status[status]
            entry['count'] += 1
            entry['total_value'] += worth
            entry['annualized_value'] += annualized
            entry['weighted_value'] += weighted_value

            if value_type not in entry['by_type']:
                entry['by_type'][value_type] = {'count': 0, 'total_value': 0, 'annualized_value': 0}
            entry['by_type'][value_type]['count'] += 1
            entry['by_type'][value_type]['total_value'] += worth
            entry['by_type'][value_type]['annualized_value'] += annualized

            total_forecast += weighted_value

            # MRR/ARR only from completed projects
            if status == 'completed':
                if value_type == 'monthly':
                    total_mrr += worth
                    total_arr += worth * 12
                elif value_type == 'yearly':
                    total_mrr += worth / 12
                    total_arr += worth
        lead_forecast = self._query(
            Lead.lead_status, func.count(Lead.id).label('count')
        ).filter(Lead.tenant_id == tenant_id, Lead.deleted_at == None).group_by(Lead.lead_status).all()
        return {
            "projects": [{
                "status": status,
                "count": data['count'],
                "total_value": round(data['total_value'], 2),
                "annualized_value": round(data['annualized_value'], 2),
                "weighted_value": round(data['weighted_value'], 2),
                "weight": data['weight'],
                "by_type": {
                    vt: {
                        "count": vdata['count'],
                        "total_value": round(vdata['total_value'], 2),
                        "annualized_value": round(vdata['annualized_value'], 2),
                    }
                    for vt, vdata in data['by_type'].items()
                }
            } for status, data in forecast_by_status.items()],
            "total_weighted_forecast": round(total_forecast, 2),
            "mrr_from_projects": round(total_mrr, 2),
            "arr_from_projects": round(total_arr, 2),
            "lead_pipeline": [{"status": row.lead_status, "count": row.count} for row in lead_forecast]
        }

    def subscription_income_report(self, start_date=None, end_date=None):
        start_date, end_date = self._date_range(start_date, end_date)
        tenant_id = self.principal.tenant_id
        filters = [
            Subscription.tenant_id == tenant_id,
            Subscription.status == "active",
        ]
        if start_date:
            filters.append(Subscription.start_date >= start_date)
        if end_date:
            filters.append(Subscription.start_date <= end_date)
        subs = self._query(Subscription).filter(*filters).all()
        monthly_revenue = sum(s.price for s in subs if s.billing_cycle == "monthly")
        yearly_revenue = sum(s.price for s in subs if s.billing_cycle == "yearly")
        mrr = monthly_revenue + (yearly_revenue / 12)
        arr = (monthly_revenue * 12) + yearly_revenue
        client_map: dict = {}
        for s in subs:
            cid = s.client_id
            if cid not in client_map:
                client_map[cid] = {
                    "client_id": cid,
                    "client_name": s.client.name if s.client else None,
                    "subscription_count": 0,
                    "monthly_total": 0.0,
                    "yearly_total": 0.0,
                    "mrr": 0.0,
                }
            entry = client_map[cid]
            entry["subscription_count"] += 1
            if s.billing_cycle == "monthly":
                entry["monthly_total"] += s.price
                entry["mrr"] += s.price
            else:
                entry["yearly_total"] += s.price
                entry["mrr"] += s.price / 12
        clients_list = sorted(client_map.values(), key=lambda x: x["mrr"], reverse=True)
        return {
            "active_subscriptions": len(subs),
            "mrr": round(mrr, 2),
            "arr": round(arr, 2),
            "monthly_subscription_count": sum(1 for s in subs if s.billing_cycle == "monthly"),
            "yearly_subscription_count": sum(1 for s in subs if s.billing_cycle == "yearly"),
            "monthly_revenue": round(monthly_revenue, 2),
            "yearly_revenue": round(yearly_revenue, 2),
            "by_client": clients_list,
        }

    def upcoming_renewals_report(self, days_ahead=60, cycle_filter=None):
        self._bounded_integer(days_ahead, "days", lower=0)
        tenant_id = self.principal.tenant_id
        now = datetime.utcnow()
        future_cutoff = now + timedelta(days=days_ahead)
        if cycle_filter == "monthly":
            cycles = ["monthly"]
        elif cycle_filter == "all":
            cycles = ["monthly", "yearly"]
        else:
            cycles = ["yearly"]
        subs = self._query(Subscription).filter(
            Subscription.tenant_id == tenant_id,
            Subscription.status == "active",
            Subscription.billing_cycle.in_(cycles),
            Subscription.renewal_date != None,
            Subscription.renewal_date >= now,
            Subscription.renewal_date <= future_cutoff,
        ).order_by(Subscription.renewal_date.asc()).all()
        return {
            "days_ahead": days_ahead,
            "upcoming_renewals": [
                {
                    "subscription_id": s.id,
                    "client_id": s.client_id,
                    "client_name": s.client.name if s.client else None,
                    "plan_name": s.plan_name,
                    "price": s.price,
                    "billing_cycle": s.billing_cycle,
                    "renewal_date": s.renewal_date.isoformat() + "Z" if s.renewal_date else None,
                    "days_until_renewal": (s.renewal_date - now).days if s.renewal_date else None,
                }
                for s in subs
            ],
            "total": len(subs),
        }

    def converted_leads_report(self, start_date=None, end_date=None):
        start_date, end_date = self._date_range(start_date, end_date)
        tenant_id = self.principal.tenant_id
        filters = [
            Lead.tenant_id == tenant_id,
            Lead.lead_status == 'won',
        ]
        if start_date:
            filters.append(Lead.converted_on >= start_date)
        if end_date:
            filters.append(Lead.converted_on <= end_date)
        leads = self._query(
            Lead.id, Lead.name, Lead.lead_source, Lead.created_at,
            Lead.converted_on, Lead.assigned_to
        ).filter(*filters).order_by(Lead.converted_on.desc().nullslast()).all()
        lead_ids = [l.id for l in leads]
        client_map = {}
        if lead_ids:
            clients = self._query(
                Client.source_lead_id, Client.id, Client.name
            ).filter(
                Client.tenant_id == tenant_id,
                Client.source_lead_id.in_(lead_ids)
            ).all()
            client_map = {c.source_lead_id: {"id": c.id, "name": c.name} for c in clients}
        user_ids = {l.assigned_to for l in leads if l.assigned_to}
        user_map = {}
        if user_ids:
            users = self._query(User.id, User.email).filter(User.id.in_(user_ids)).all()
            user_map = {u.id: u.email for u in users}
        results = []
        for lead in leads:
            client = client_map.get(lead.id)
            days_in_pipeline = None
            if lead.created_at and lead.converted_on:
                days_in_pipeline = (lead.converted_on - lead.created_at).days

            results.append({
                "lead_id": lead.id,
                "name": lead.name,
                "lead_source": lead.lead_source,
                "converted_on": lead.converted_on.isoformat() if lead.converted_on else None,
                "days_in_pipeline": days_in_pipeline,
                "client_id": client["id"] if client else None,
                "client_name": client["name"] if client else None,
                "assigned_to": user_map.get(lead.assigned_to),
            })
        return {
            "converted_leads": results,
            "total": len(results),
        }
