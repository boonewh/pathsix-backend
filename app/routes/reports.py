"""HTTP adapters for tenant-bound admin reports."""
from quart import Blueprint, jsonify, request
from app.database import SessionLocal
from app.services.reports import ReportService
from app.utils.auth_utils import requires_auth

reports_bp = Blueprint("reports", __name__, url_prefix="/api/reports")


def _respond(operation):
    with SessionLocal() as session:
        try:
            return jsonify(operation(ReportService(session, request.principal)))
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except (ValueError, OverflowError):
            return jsonify({"error": "Use a valid date range, user and report parameters."}), 400

@reports_bp.route("/sales-activity", methods=["GET"])
@requires_auth(roles=["admin"])
async def sales_activity_report():
    return _respond(lambda service: service.sales_activity_report(
        start_date=request.args.get('start_date'),
        end_date=request.args.get('end_date'),
        user_id=int(request.args['user_id']) if request.args.get('user_id') else None,
        page=int(request.args.get('page', 1)),
    ))


@reports_bp.route("", methods=["GET"])
@reports_bp.route("/", methods=["GET"])
@requires_auth(roles=["admin"])
async def get_reports():
    return _respond(lambda service: service.get_reports(
        start_date=request.args.get('start_date'),
        end_date=request.args.get('end_date'),
    ))


@reports_bp.route("/summary", methods=["POST"])
@requires_auth(roles=["admin"])
async def summary_report():
    data = await request.get_json()
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid request body"}), 400
    return _respond(lambda service: service.get_reports(
        start_date=data.get('start_date'),
        end_date=data.get('end_date'),
    ))


@reports_bp.route("/pipeline", methods=["GET"])
@requires_auth(roles=["admin"])
async def sales_pipeline():
    return _respond(lambda service: service.sales_pipeline(
        start_date=request.args.get('start_date'),
        end_date=request.args.get('end_date'),
        user_filter=int(request.args['user_id']) if request.args.get('user_id') else None,
    ))


@reports_bp.route("/lead-source", methods=["GET"])
@requires_auth(roles=["admin"])
async def lead_source_report():
    return _respond(lambda service: service.lead_source_report(
        start_date=request.args.get('start_date'),
        end_date=request.args.get('end_date'),
    ))


@reports_bp.route("/conversion-rate", methods=["GET"])
@requires_auth(roles=["admin"])
async def conversion_rate_report():
    return _respond(lambda service: service.conversion_rate_report(
        start_date=request.args.get('start_date'),
        end_date=request.args.get('end_date'),
    ))


@reports_bp.route("/revenue-by-client", methods=["GET"])
@requires_auth(roles=["admin"])
async def revenue_by_client():
    return _respond(lambda service: service.revenue_by_client(
        start_date=request.args.get('start_date'),
        end_date=request.args.get('end_date'),
        limit=int(request.args.get('limit', 50)),
    ))


@reports_bp.route("/user-activity", methods=["GET"])
@requires_auth(roles=["admin"])
async def user_activity_report():
    return _respond(lambda service: service.user_activity_report(
        start_date=request.args.get('start_date'),
        end_date=request.args.get('end_date'),
    ))


@reports_bp.route("/follow-ups", methods=["GET"])
@requires_auth(roles=["admin"])
async def follow_up_report():
    return _respond(lambda service: service.follow_up_report(
        days_threshold=int(request.args.get('inactive_days', 30)),
    ))


@reports_bp.route("/client-retention", methods=["GET"])
@requires_auth(roles=["admin"])
async def client_retention_report():
    return _respond(lambda service: service.client_retention_report(
        start_date=request.args.get('start_date'),
        end_date=request.args.get('end_date'),
    ))


@reports_bp.route("/project-performance", methods=["GET"])
@requires_auth(roles=["admin"])
async def project_performance_report():
    return _respond(lambda service: service.project_performance_report(
        start_date=request.args.get('start_date'),
        end_date=request.args.get('end_date'),
    ))


@reports_bp.route("/upcoming-tasks", methods=["GET"])
@requires_auth(roles=["admin"])
async def upcoming_tasks_report():
    return _respond(lambda service: service.upcoming_tasks_report(
        days_ahead=int(request.args.get('days', 30)),
        user_filter=int(request.args['user_id']) if request.args.get('user_id') else None,
    ))


@reports_bp.route("/revenue-forecast", methods=["GET"])
@requires_auth(roles=["admin"])
async def revenue_forecast_report():
    return _respond(lambda service: service.revenue_forecast_report())


@reports_bp.route("/subscriptions/income", methods=["GET"])
@requires_auth(roles=["admin"])
async def subscription_income_report():
    return _respond(lambda service: service.subscription_income_report(
        start_date=request.args.get('start_date'),
        end_date=request.args.get('end_date'),
    ))


@reports_bp.route("/subscriptions/upcoming-renewals", methods=["GET"])
@requires_auth(roles=["admin"])
async def upcoming_renewals_report():
    return _respond(lambda service: service.upcoming_renewals_report(
        days_ahead=int(request.args.get('days', 60)),
        cycle_filter=request.args.get('cycle'),
    ))


@reports_bp.route("/converted-leads", methods=["GET"])
@requires_auth(roles=["admin"])
async def converted_leads_report():
    return _respond(lambda service: service.converted_leads_report(
        start_date=request.args.get('start_date'),
        end_date=request.args.get('end_date'),
    ))
