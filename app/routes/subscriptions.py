from quart import Blueprint, request, jsonify
from datetime import datetime
from dateutil.relativedelta import relativedelta
from pydantic import ValidationError
from app.models import Subscription, Client
from app.database import SessionLocal
from app.utils.auth_utils import requires_auth
from app.schemas.subscriptions import SubscriptionCreateSchema, SubscriptionUpdateSchema
from sqlalchemy.orm import joinedload
from sqlalchemy import or_

subscriptions_bp = Blueprint("subscriptions", __name__, url_prefix="/api/subscriptions")


def _compute_renewal_date(start_date: datetime, billing_cycle: str) -> datetime:
    """Calculate the next renewal date from start_date based on billing_cycle."""
    if billing_cycle == "monthly":
        return start_date + relativedelta(months=1)
    else:  # yearly
        return start_date + relativedelta(years=1)


def _subscription_to_dict(sub: Subscription) -> dict:
    return {
        "id": sub.id,
        "tenant_id": sub.tenant_id,
        "client_id": sub.client_id,
        "client_name": sub.client.name if sub.client else None,
        "plan_name": sub.plan_name,
        "price": sub.price,
        "billing_cycle": sub.billing_cycle,
        "start_date": sub.start_date.isoformat() + "Z" if sub.start_date else None,
        "renewal_date": sub.renewal_date.isoformat() + "Z" if sub.renewal_date else None,
        "status": sub.status,
        "notes": sub.notes,
        "created_at": sub.created_at.isoformat() + "Z" if sub.created_at else None,
        "cancelled_at": sub.cancelled_at.isoformat() + "Z" if sub.cancelled_at else None,
    }


@subscriptions_bp.route("", methods=["GET"])
@subscriptions_bp.route("/", methods=["GET"])
@requires_auth()
async def list_subscriptions():
    user = request.user
    session = SessionLocal()
    try:
        client_id = request.args.get("client_id", type=int)
        status_filter = request.args.get("status")  # active, paused, cancelled, or omit for all

        query = session.query(Subscription).options(
            joinedload(Subscription.client)
        ).filter(
            Subscription.tenant_id == user.tenant_id
        )

        if client_id:
            query = query.filter(Subscription.client_id == client_id)

        if status_filter:
            query = query.filter(Subscription.status == status_filter)

        # Non-admins can only see subscriptions for their clients
        if not any(role.name == "admin" for role in user.roles):
            accessible_client_ids = session.query(Client.id).filter(
                Client.tenant_id == user.tenant_id,
                Client.deleted_at == None,
                or_(
                    Client.assigned_to == user.id,
                    Client.created_by == user.id,
                )
            ).subquery()
            query = query.filter(Subscription.client_id.in_(accessible_client_ids))

        query = query.order_by(Subscription.renewal_date.asc())
        subs = query.all()

        return jsonify({
            "subscriptions": [_subscription_to_dict(s) for s in subs],
            "total": len(subs),
        })
    finally:
        session.close()


@subscriptions_bp.route("", methods=["POST"])
@subscriptions_bp.route("/", methods=["POST"])
@requires_auth()
async def create_subscription():
    user = request.user
    raw_data = await request.get_json()

    try:
        data = SubscriptionCreateSchema(**raw_data)
    except ValidationError as e:
        return jsonify({"error": "Validation failed", "details": e.errors()}), 400

    session = SessionLocal()
    try:
        # Verify client belongs to tenant
        client = session.query(Client).filter(
            Client.id == data.client_id,
            Client.tenant_id == user.tenant_id,
            Client.deleted_at == None,
        ).first()
        if not client:
            return jsonify({"error": "Client not found"}), 404

        renewal_date = data.renewal_date or _compute_renewal_date(data.start_date, data.billing_cycle)

        sub = Subscription(
            tenant_id=user.tenant_id,
            client_id=data.client_id,
            plan_name=data.plan_name,
            price=data.price,
            billing_cycle=data.billing_cycle,
            start_date=data.start_date,
            renewal_date=renewal_date,
            status=data.status,
            notes=data.notes,
            created_by=user.id,
            created_at=datetime.utcnow(),
        )
        session.add(sub)
        session.commit()
        session.refresh(sub)

        # Load client relationship for response
        session.refresh(sub)
        sub.client  # trigger lazy load before session closes

        return jsonify(_subscription_to_dict(sub)), 201
    finally:
        session.close()


@subscriptions_bp.route("/<int:sub_id>", methods=["GET"])
@requires_auth()
async def get_subscription(sub_id):
    user = request.user
    session = SessionLocal()
    try:
        sub = session.query(Subscription).options(
            joinedload(Subscription.client)
        ).filter(
            Subscription.id == sub_id,
            Subscription.tenant_id == user.tenant_id,
        ).first()

        if not sub:
            return jsonify({"error": "Subscription not found"}), 404

        return jsonify(_subscription_to_dict(sub))
    finally:
        session.close()


@subscriptions_bp.route("/<int:sub_id>", methods=["PUT"])
@requires_auth()
async def update_subscription(sub_id):
    user = request.user
    raw_data = await request.get_json()

    try:
        data = SubscriptionUpdateSchema(**raw_data)
    except ValidationError as e:
        return jsonify({"error": "Validation failed", "details": e.errors()}), 400

    session = SessionLocal()
    try:
        sub = session.query(Subscription).options(
            joinedload(Subscription.client)
        ).filter(
            Subscription.id == sub_id,
            Subscription.tenant_id == user.tenant_id,
        ).first()

        if not sub:
            return jsonify({"error": "Subscription not found"}), 404

        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(sub, field, value)

        # If status changed to cancelled, record the cancellation time
        if update_data.get("status") == "cancelled" and sub.cancelled_at is None:
            sub.cancelled_at = datetime.utcnow()
        elif update_data.get("status") in ("active", "paused"):
            sub.cancelled_at = None

        # If billing_cycle or start_date changed and no explicit renewal_date, recompute
        if ("billing_cycle" in update_data or "start_date" in update_data) and "renewal_date" not in update_data:
            sub.renewal_date = _compute_renewal_date(sub.start_date, sub.billing_cycle)

        sub.updated_by = user.id
        sub.updated_at = datetime.utcnow()

        session.commit()
        session.refresh(sub)
        sub.client  # trigger lazy load

        return jsonify(_subscription_to_dict(sub))
    finally:
        session.close()


@subscriptions_bp.route("/<int:sub_id>", methods=["DELETE"])
@requires_auth()
async def delete_subscription(sub_id):
    user = request.user
    session = SessionLocal()
    try:
        sub = session.query(Subscription).filter(
            Subscription.id == sub_id,
            Subscription.tenant_id == user.tenant_id,
        ).first()

        if not sub:
            return jsonify({"error": "Subscription not found"}), 404

        session.delete(sub)
        session.commit()
        return jsonify({"message": "Subscription deleted"})
    finally:
        session.close()


@subscriptions_bp.route("/<int:sub_id>/renew", methods=["POST"])
@requires_auth()
async def renew_subscription(sub_id):
    """Advance the renewal date by one billing cycle."""
    user = request.user
    session = SessionLocal()
    try:
        sub = session.query(Subscription).options(
            joinedload(Subscription.client)
        ).filter(
            Subscription.id == sub_id,
            Subscription.tenant_id == user.tenant_id,
        ).first()

        if not sub:
            return jsonify({"error": "Subscription not found"}), 404

        if sub.renewal_date:
            sub.renewal_date = _compute_renewal_date(sub.renewal_date, sub.billing_cycle)
        else:
            sub.renewal_date = _compute_renewal_date(sub.start_date, sub.billing_cycle)

        if sub.status != "active":
            sub.status = "active"
            sub.cancelled_at = None

        sub.updated_by = user.id
        sub.updated_at = datetime.utcnow()
        session.commit()
        session.refresh(sub)
        sub.client  # trigger lazy load

        return jsonify(_subscription_to_dict(sub))
    finally:
        session.close()
