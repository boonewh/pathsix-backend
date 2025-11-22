from quart import Blueprint, jsonify, request
from sqlalchemy import func
from datetime import datetime
from app.database import SessionLocal
from app.models import Lead, Project
from app.utils.auth_utils import requires_auth
from dateutil.parser import parse as parse_date

reports_bp = Blueprint("reports", __name__, url_prefix="/api/reports")

from dateutil.parser import parse as parse_date

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
            Lead.lead_status == "converted"
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

