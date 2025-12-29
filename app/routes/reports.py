from quart import Blueprint, jsonify, request
from sqlalchemy import func, and_, or_, case, distinct, cast, Date
from datetime import datetime, timedelta
from app.database import SessionLocal
from app.models import Lead, Project, Client, Interaction, User, ActivityLog
from app.utils.auth_utils import requires_auth
from dateutil.parser import parse as parse_date

reports_bp = Blueprint("reports", __name__, url_prefix="/api/reports")


# ============================================================================
# LEGACY ENDPOINTS (keeping for backwards compatibility)
# ============================================================================
@reports_bp.route("", methods=["GET"])
@reports_bp.route("/", methods=["GET"])
@requires_auth()
async def get_reports():
    user = request.user
    session = SessionLocal()
    try:
        tenant_id = user.tenant_id
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")

        filters = [Lead.tenant_id == tenant_id, Lead.deleted_at == None]
        project_filters = [Project.tenant_id == tenant_id]

        if start_date:
            dt_start = parse_date(start_date)
            filters.append(Lead.created_at >= dt_start)
            project_filters.append(Project.created_at >= dt_start)

        if end_date:
            dt_end = parse_date(end_date)
            filters.append(Lead.created_at <= dt_end)
            project_filters.append(Project.created_at <= dt_end)

        # Total leads
        total_leads = session.query(func.count(Lead.id)).filter(*filters).scalar()

        # Converted leads
        converted_leads = session.query(func.count(Lead.id)).filter(
            *filters,
            Lead.lead_status == "closed"
        ).scalar()

        # Total projects
        total_projects = session.query(func.count(Project.id)).filter(*project_filters).scalar()

        # Won projects
        won_projects = session.query(func.count(Project.id)).filter(
            *project_filters,
            Project.project_status == "won"
        ).scalar()

        # Lost projects
        lost_projects = session.query(func.count(Project.id)).filter(
            *project_filters,
            Project.project_status == "lost"
        ).scalar()

        # Total won value
        total_won_value = session.query(func.coalesce(func.sum(Project.project_worth), 0)).filter(
            *project_filters,
            Project.project_status == "won"
        ).scalar()

        return jsonify({
            "lead_count": total_leads,
            "converted_leads": converted_leads,
            "project_count": total_projects,
            "won_projects": won_projects,
            "lost_projects": lost_projects,
            "total_won_value": total_won_value
        })
    finally:
        session.close()

@reports_bp.route("/summary", methods=["POST"])
@requires_auth()
async def summary_report():
    user = request.user
    session = SessionLocal()
    try:
        tenant_id = user.tenant_id
        data = await request.get_json()
        start_date = data.get("start_date")
        end_date = data.get("end_date")

        filters = [Lead.tenant_id == tenant_id, Lead.deleted_at == None]
        project_filters = [Project.tenant_id == tenant_id]

        if start_date:
            dt_start = parse_date(start_date)
            filters.append(Lead.created_at >= dt_start)
            project_filters.append(Project.created_at >= dt_start)

        if end_date:
            dt_end = parse_date(end_date)
            filters.append(Lead.created_at <= dt_end)
            project_filters.append(Project.created_at <= dt_end)

        total_leads = session.query(func.count(Lead.id)).filter(*filters).scalar()
        converted_leads = session.query(func.count(Lead.id)).filter(
            *filters, Lead.lead_status == "converted"
        ).scalar()

        total_projects = session.query(func.count(Project.id)).filter(*project_filters).scalar()
        won_projects = session.query(func.count(Project.id)).filter(
            *project_filters, Project.project_status == "won"
        ).scalar()
        lost_projects = session.query(func.count(Project.id)).filter(
            *project_filters, Project.project_status == "lost"
        ).scalar()
        total_won_value = session.query(func.coalesce(func.sum(Project.project_worth), 0)).filter(
            *project_filters, Project.project_status == "won"
        ).scalar()

        return jsonify({
            "lead_count": total_leads,
            "converted_leads": converted_leads,
            "project_count": total_projects,
            "won_projects": won_projects,
            "lost_projects": lost_projects,
            "total_won_value": total_won_value
        })
    finally:
        session.close()


# ============================================================================
# NEW COMPREHENSIVE REPORTING ENDPOINTS
# ============================================================================

# 1. SALES PIPELINE REPORT
@reports_bp.route("/pipeline", methods=["GET"])
@requires_auth()
async def sales_pipeline():
    """Tracks leads by stage and value."""
    user = request.user
    session = SessionLocal()
    try:
        tenant_id = user.tenant_id
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        user_filter = request.args.get("user_id")
        
        lead_filters = [Lead.tenant_id == tenant_id, Lead.deleted_at == None]
        if start_date:
            lead_filters.append(Lead.created_at >= parse_date(start_date))
        if end_date:
            lead_filters.append(Lead.created_at <= parse_date(end_date))
        if user_filter and "admin" in [r.name for r in user.roles]:
            lead_filters.append(Lead.assigned_to == int(user_filter))
        
        lead_pipeline = session.query(
            Lead.lead_status,
            func.count(Lead.id).label('count')
        ).filter(*lead_filters).group_by(Lead.lead_status).all()
        
        project_filters = [Project.tenant_id == tenant_id, Project.deleted_at == None]
        if start_date:
            project_filters.append(Project.created_at >= parse_date(start_date))
        if end_date:
            project_filters.append(Project.created_at <= parse_date(end_date))
        
        project_pipeline = session.query(
            Project.project_status,
            func.count(Project.id).label('count'),
            func.coalesce(func.sum(Project.project_worth), 0).label('total_value')
        ).filter(*project_filters).group_by(Project.project_status).all()
        
        return jsonify({
            "leads": [{"status": row.lead_status, "count": row.count} for row in lead_pipeline],
            "projects": [{
                "status": row.project_status,
                "count": row.count,
                "total_value": float(row.total_value)
            } for row in project_pipeline]
        })
    finally:
        session.close()


# 2. LEAD SOURCE REPORT
@reports_bp.route("/lead-source", methods=["GET"])
@requires_auth()
async def lead_source_report():
    """Shows which sources bring in the best leads and highest conversions."""
    user = request.user
    session = SessionLocal()
    try:
        tenant_id = user.tenant_id
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        
        filters = [Lead.tenant_id == tenant_id, Lead.deleted_at == None]
        if start_date:
            filters.append(Lead.created_at >= parse_date(start_date))
        if end_date:
            filters.append(Lead.created_at <= parse_date(end_date))
        
        results = session.query(
            Lead.lead_source,
            func.count(Lead.id).label('total_leads'),
            func.sum(case((Lead.lead_status == 'closed', 1), else_=0)).label('converted'),
            func.sum(case((Lead.lead_status == 'qualified', 1), else_=0)).label('qualified')
        ).filter(*filters).group_by(Lead.lead_source).all()
        
        return jsonify({
            "sources": [{
                "source": row.lead_source or "Unknown",
                "total_leads": row.total_leads,
                "converted": row.converted,
                "qualified": row.qualified,
                "conversion_rate": round((row.converted / row.total_leads * 100), 2) if row.total_leads > 0 else 0
            } for row in results]
        })
    finally:
        session.close()


# 3. CONVERSION RATE REPORT
@reports_bp.route("/conversion-rate", methods=["GET"])
@requires_auth()
async def conversion_rate_report():
    """Measures how well leads move through funnel and who's closing them."""
    user = request.user
    session = SessionLocal()
    try:
        tenant_id = user.tenant_id
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        
        filters = [Lead.tenant_id == tenant_id, Lead.deleted_at == None]
        if start_date:
            filters.append(Lead.created_at >= parse_date(start_date))
        if end_date:
            filters.append(Lead.created_at <= parse_date(end_date))
        
        total_leads = session.query(func.count(Lead.id)).filter(*filters).scalar()
        converted_leads = session.query(func.count(Lead.id)).filter(*filters, Lead.lead_status == 'closed').scalar()
        overall_rate = round((converted_leads / total_leads * 100), 2) if total_leads > 0 else 0
        
        by_user = []
        if "admin" in [r.name for r in user.roles]:
            user_stats = session.query(
                Lead.assigned_to, User.email,
                func.count(Lead.id).label('total'),
                func.sum(case((Lead.lead_status == 'closed', 1), else_=0)).label('converted')
            ).join(User, Lead.assigned_to == User.id).filter(*filters).group_by(Lead.assigned_to, User.email).all()
            
            by_user = [{
                "user_id": row.assigned_to,
                "user_email": row.email,
                "total_leads": row.total,
                "converted": row.converted,
                "conversion_rate": round((row.converted / row.total * 100), 2) if row.total > 0 else 0
            } for row in user_stats]
        
        # Calculate average days to convert (database-agnostic)
        # PostgreSQL: EXTRACT(EPOCH FROM (date1 - date2)) / 86400
        # SQLite: julianday(date1) - julianday(date2)
        from app.config import SQLALCHEMY_DATABASE_URI
        if 'postgresql' in SQLALCHEMY_DATABASE_URI or 'postgres' in SQLALCHEMY_DATABASE_URI:
            # PostgreSQL approach: EXTRACT(EPOCH FROM (converted_on - created_at)) / 86400
            avg_days = session.query(
                func.avg(
                    func.extract('epoch', Lead.converted_on - Lead.created_at) / 86400
                )
            ).filter(*filters, Lead.lead_status == 'closed', Lead.converted_on != None).scalar()
        else:
            # SQLite approach: julianday
            avg_days = session.query(
                func.avg(func.julianday(Lead.converted_on) - func.julianday(Lead.created_at))
            ).filter(*filters, Lead.lead_status == 'closed', Lead.converted_on != None).scalar()
        
        return jsonify({
            "overall": {
                "total_leads": total_leads,
                "converted_leads": converted_leads,
                "conversion_rate": overall_rate,
                "avg_days_to_convert": round(avg_days, 1) if avg_days else None
            },
            "by_user": by_user
        })
    finally:
        session.close()


# 4. REVENUE BY CLIENT REPORT
@reports_bp.route("/revenue-by-client", methods=["GET"])
@requires_auth()
async def revenue_by_client():
    """Aggregates all project totals per client."""
    user = request.user
    session = SessionLocal()
    try:
        tenant_id = user.tenant_id
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        limit = int(request.args.get("limit", 50))
        
        filters = [Project.tenant_id == tenant_id, Project.deleted_at == None]
        if start_date:
            filters.append(Project.created_at >= parse_date(start_date))
        if end_date:
            filters.append(Project.created_at <= parse_date(end_date))
        
        client_revenue = session.query(
            Client.id, Client.name,
            func.count(Project.id).label('project_count'),
            func.coalesce(func.sum(case((Project.project_status == 'won', Project.project_worth), else_=0)), 0).label('won_value'),
            func.coalesce(func.sum(case((Project.project_status == 'pending', Project.project_worth), else_=0)), 0).label('pending_value'),
            func.coalesce(func.sum(Project.project_worth), 0).label('total_value')
        ).join(Project, Client.id == Project.client_id).filter(*filters).group_by(
            Client.id, Client.name
        ).order_by(func.sum(Project.project_worth).desc()).limit(limit).all()
        
        return jsonify({
            "clients": [{
                "client_id": row.id,
                "client_name": row.name,
                "project_count": row.project_count,
                "won_value": float(row.won_value),
                "pending_value": float(row.pending_value),
                "total_value": float(row.total_value)
            } for row in client_revenue]
        })
    finally:
        session.close()


# 5. USER ACTIVITY REPORT
@reports_bp.route("/user-activity", methods=["GET"])
@requires_auth(roles=["admin"])
async def user_activity_report():
    """Tracks each team member's engagement. Admin only."""
    session = SessionLocal()
    try:
        user = request.user
        tenant_id = user.tenant_id
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        
        date_filter = []
        if start_date:
            date_filter.append(Interaction.contact_date >= parse_date(start_date))
        if end_date:
            date_filter.append(Interaction.contact_date <= parse_date(end_date))
        
        users = session.query(User).filter(User.tenant_id == tenant_id, User.is_active == True).all()
        
        user_stats = []
        for u in users:
            interaction_count = session.query(func.count(Interaction.id)).filter(
                Interaction.tenant_id == tenant_id,
                or_(
                    Interaction.client_id.in_(session.query(Client.id).filter(Client.assigned_to == u.id, Client.tenant_id == tenant_id)),
                    Interaction.lead_id.in_(session.query(Lead.id).filter(Lead.assigned_to == u.id, Lead.tenant_id == tenant_id))
                ),
                *date_filter
            ).scalar()
            
            leads_assigned = session.query(func.count(Lead.id)).filter(
                Lead.tenant_id == tenant_id, Lead.assigned_to == u.id, Lead.deleted_at == None
            ).scalar()
            
            clients_assigned = session.query(func.count(Client.id)).filter(
                Client.tenant_id == tenant_id, Client.assigned_to == u.id, Client.deleted_at == None
            ).scalar()
            
            activity_count = session.query(func.count(ActivityLog.id)).filter(
                ActivityLog.tenant_id == tenant_id,
                ActivityLog.user_id == u.id,
                *([ActivityLog.timestamp >= parse_date(start_date)] if start_date else []),
                *([ActivityLog.timestamp <= parse_date(end_date)] if end_date else [])
            ).scalar()
            
            user_stats.append({
                "user_id": u.id,
                "email": u.email,
                "interactions": interaction_count,
                "leads_assigned": leads_assigned,
                "clients_assigned": clients_assigned,
                "activity_count": activity_count
            })
        
        return jsonify({"users": user_stats})
    finally:
        session.close()


# 6. FOLLOW-UP / INACTIVITY REPORT
@reports_bp.route("/follow-ups", methods=["GET"])
@requires_auth()
async def follow_up_report():
    """Highlights contacts overdue for outreach or with no recent activity."""
    user = request.user
    session = SessionLocal()
    try:
        tenant_id = user.tenant_id
        now = datetime.utcnow()
        days_threshold = int(request.args.get("inactive_days", 30))
        
        overdue_filters = [
            Interaction.tenant_id == tenant_id,
            Interaction.follow_up != None,
            Interaction.follow_up < now,
            Interaction.followup_status.in_(['pending', 'rescheduled'])
        ]
        
        overdue = session.query(
            Interaction.id, Interaction.client_id, Interaction.lead_id,
            Interaction.follow_up, Interaction.summary,
            Client.name.label('client_name'), Lead.name.label('lead_name')
        ).outerjoin(Client, Interaction.client_id == Client.id).outerjoin(
            Lead, Interaction.lead_id == Lead.id
        ).filter(*overdue_filters).order_by(Interaction.follow_up.asc()).all()
        
        inactive_threshold = now - timedelta(days=days_threshold)
        
        recent_client_interactions = session.query(distinct(Interaction.client_id)).filter(
            Interaction.tenant_id == tenant_id,
            Interaction.client_id != None,
            Interaction.contact_date >= inactive_threshold
        ).subquery()
        
        inactive_clients = session.query(
            Client.id, Client.name,
            func.max(Interaction.contact_date).label('last_interaction')
        ).outerjoin(Interaction, Client.id == Interaction.client_id).filter(
            Client.tenant_id == tenant_id,
            Client.deleted_at == None,
            ~Client.id.in_(recent_client_interactions)
        ).group_by(Client.id, Client.name).all()
        
        recent_lead_interactions = session.query(distinct(Interaction.lead_id)).filter(
            Interaction.tenant_id == tenant_id,
            Interaction.lead_id != None,
            Interaction.contact_date >= inactive_threshold
        ).subquery()
        
        inactive_leads = session.query(
            Lead.id, Lead.name,
            func.max(Interaction.contact_date).label('last_interaction')
        ).outerjoin(Interaction, Lead.id == Interaction.lead_id).filter(
            Lead.tenant_id == tenant_id,
            Lead.deleted_at == None,
            Lead.lead_status.in_(['open', 'qualified']),
            ~Lead.id.in_(recent_lead_interactions)
        ).group_by(Lead.id, Lead.name).all()
        
        return jsonify({
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
        })
    finally:
        session.close()


# 7. CLIENT RETENTION REPORT
@reports_bp.route("/client-retention", methods=["GET"])
@requires_auth()
async def client_retention_report():
    """Shows how many clients renewed, stayed active, or dropped off over time."""
    user = request.user
    session = SessionLocal()
    try:
        tenant_id = user.tenant_id
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        
        filters = [Client.tenant_id == tenant_id]
        if start_date:
            filters.append(Client.created_at >= parse_date(start_date))
        if end_date:
            filters.append(Client.created_at <= parse_date(end_date))
        
        status_breakdown = session.query(
            Client.status, func.count(Client.id).label('count')
        ).filter(*filters, Client.deleted_at == None).group_by(Client.status).all()
        
        churned_count = session.query(func.count(Client.id)).filter(
            Client.tenant_id == tenant_id,
            Client.deleted_at != None,
            *([Client.deleted_at >= parse_date(start_date)] if start_date else []),
            *([Client.deleted_at <= parse_date(end_date)] if end_date else [])
        ).scalar()
        
        total_active = session.query(func.count(Client.id)).filter(*filters, Client.deleted_at == None).scalar()
        
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        active_with_interactions = session.query(func.count(distinct(Interaction.client_id))).filter(
            Interaction.tenant_id == tenant_id,
            Interaction.client_id != None,
            Interaction.contact_date >= thirty_days_ago
        ).scalar()
        
        return jsonify({
            "status_breakdown": [{"status": row.status, "count": row.count} for row in status_breakdown],
            "total_active": total_active,
            "churned": churned_count,
            "active_with_recent_interactions": active_with_interactions,
            "retention_rate": round((total_active / (total_active + churned_count) * 100), 2) if (total_active + churned_count) > 0 else 0
        })
    finally:
        session.close()


# 8. PROJECT PERFORMANCE REPORT
@reports_bp.route("/project-performance", methods=["GET"])
@requires_auth()
async def project_performance_report():
    """Summarizes project outcomes, durations, or success rates."""
    user = request.user
    session = SessionLocal()
    try:
        tenant_id = user.tenant_id
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        
        filters = [Project.tenant_id == tenant_id, Project.deleted_at == None]
        if start_date:
            filters.append(Project.created_at >= parse_date(start_date))
        if end_date:
            filters.append(Project.created_at <= parse_date(end_date))
        
        status_counts = session.query(
            Project.project_status,
            func.count(Project.id).label('count'),
            func.coalesce(func.sum(Project.project_worth), 0).label('total_value')
        ).filter(*filters).group_by(Project.project_status).all()
        
        total_projects = session.query(func.count(Project.id)).filter(*filters).scalar()
        won_projects = session.query(func.count(Project.id)).filter(*filters, Project.project_status == 'won').scalar()
        win_rate = round((won_projects / total_projects * 100), 2) if total_projects > 0 else 0
        
        # Calculate average project duration (database-agnostic)
        from app.config import SQLALCHEMY_DATABASE_URI
        if 'postgresql' in SQLALCHEMY_DATABASE_URI or 'postgres' in SQLALCHEMY_DATABASE_URI:
            # PostgreSQL approach: EXTRACT(EPOCH FROM (project_end - project_start)) / 86400
            avg_duration = session.query(
                func.avg(
                    func.extract('epoch', Project.project_end - Project.project_start) / 86400
                )
            ).filter(*filters, Project.project_start != None, Project.project_end != None, Project.project_status == 'won').scalar()
        else:
            # SQLite approach: julianday
            avg_duration = session.query(
                func.avg(func.julianday(Project.project_end) - func.julianday(Project.project_start))
            ).filter(*filters, Project.project_start != None, Project.project_end != None, Project.project_status == 'won').scalar()
        
        avg_value = session.query(func.avg(Project.project_worth)).filter(*filters, Project.project_worth != None).scalar()
        
        return jsonify({
            "status_breakdown": [{
                "status": row.project_status,
                "count": row.count,
                "total_value": float(row.total_value)
            } for row in status_counts],
            "total_projects": total_projects,
            "win_rate": win_rate,
            "avg_duration_days": round(avg_duration, 1) if avg_duration else None,
            "avg_project_value": round(float(avg_value), 2) if avg_value else None
        })
    finally:
        session.close()


# 9. UPCOMING TASKS REPORT
@reports_bp.route("/upcoming-tasks", methods=["GET"])
@requires_auth()
async def upcoming_tasks_report():
    """Lists upcoming meetings, calls, or follow-ups for the team."""
    user = request.user
    session = SessionLocal()
    try:
        tenant_id = user.tenant_id
        days_ahead = int(request.args.get("days", 30))
        user_filter = request.args.get("user_id")
        
        now = datetime.utcnow()
        future_date = now + timedelta(days=days_ahead)
        
        filters = [
            Interaction.tenant_id == tenant_id,
            Interaction.follow_up != None,
            Interaction.follow_up >= now,
            Interaction.follow_up <= future_date,
            Interaction.followup_status.in_(['pending', 'rescheduled'])
        ]
        
        if user_filter and "admin" in [r.name for r in user.roles]:
            filters.extend([or_(
                Interaction.client_id.in_(session.query(Client.id).filter(Client.assigned_to == int(user_filter))),
                Interaction.lead_id.in_(session.query(Lead.id).filter(Lead.assigned_to == int(user_filter)))
            )])
        elif "admin" not in [r.name for r in user.roles]:
            filters.extend([or_(
                Interaction.client_id.in_(session.query(Client.id).filter(Client.assigned_to == user.id)),
                Interaction.lead_id.in_(session.query(Lead.id).filter(Lead.assigned_to == user.id))
            )])
        
        upcoming = session.query(
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
            users = session.query(User.id, User.email).filter(User.id.in_(assigned_user_ids)).all()
            user_map = {u.id: u.email for u in users}
        
        return jsonify({
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
        })
    finally:
        session.close()


# 10. REVENUE FORECAST REPORT
@reports_bp.route("/revenue-forecast", methods=["GET"])
@requires_auth()
async def revenue_forecast_report():
    """Predicts likely future income based on weighted pipeline stages."""
    user = request.user
    session = SessionLocal()
    try:
        tenant_id = user.tenant_id
        
        WEIGHTS = {'pending': 0.3, 'won': 1.0, 'lost': 0.0}
        
        projects = session.query(
            Project.project_status, Project.project_worth
        ).filter(
            Project.tenant_id == tenant_id,
            Project.deleted_at == None,
            Project.project_worth != None
        ).all()
        
        forecast_by_status = {}
        total_forecast = 0
        
        for project in projects:
            status = project.project_status
            worth = float(project.project_worth or 0)
            weight = WEIGHTS.get(status, 0)
            weighted_value = worth * weight
            
            if status not in forecast_by_status:
                forecast_by_status[status] = {'count': 0, 'total_value': 0, 'weighted_value': 0, 'weight': weight}
            
            forecast_by_status[status]['count'] += 1
            forecast_by_status[status]['total_value'] += worth
            forecast_by_status[status]['weighted_value'] += weighted_value
            total_forecast += weighted_value
        
        lead_forecast = session.query(
            Lead.lead_status, func.count(Lead.id).label('count')
        ).filter(Lead.tenant_id == tenant_id, Lead.deleted_at == None).group_by(Lead.lead_status).all()
        
        return jsonify({
            "projects": [{
                "status": status,
                "count": data['count'],
                "total_value": round(data['total_value'], 2),
                "weighted_value": round(data['weighted_value'], 2),
                "weight": data['weight']
            } for status, data in forecast_by_status.items()],
            "total_weighted_forecast": round(total_forecast, 2),
            "lead_pipeline": [{"status": row.lead_status, "count": row.count} for row in lead_forecast]
        })
    finally:
        session.close()
