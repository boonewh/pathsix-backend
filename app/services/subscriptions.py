"""Subscription lifecycle with inherited active-client access and caller-owned commits."""

from datetime import datetime
from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import joinedload
from app.models import Subscription, Client
from app.schemas.subscriptions import (
    SubscriptionCreateSchema,
    SubscriptionUpdateSchema,
    SUBSCRIPTION_STATUSES,
)
from app.services.base import TenantService
from app.services.access import owned_record_filter
from app.services.errors import RecordNotFound


def compute_renewal_date(start_date, billing_cycle):
    if billing_cycle not in ("monthly", "yearly"):
        raise ValueError("Invalid billing cycle")
    try:
        return start_date + relativedelta(
            **({"months": 1} if billing_cycle == "monthly" else {"years": 1})
        )
    except (ValueError, OverflowError):
        raise ValueError("Renewal date is outside the supported range") from None


class SubscriptionService(TenantService):
    def _get(self, sub_id):
        sub = (
            self._query(Subscription)
            .options(joinedload(Subscription.client))
            .filter(Subscription.id == sub_id)
            .first()
        )
        if sub is None:
            raise RecordNotFound("Subscription not found")
        self._require_record(Client, sub.client_id)
        return sub

    @staticmethod
    def _serialize(sub):
        result = {
            field: getattr(sub, field)
            for field in (
                "id",
                "tenant_id",
                "client_id",
                "plan_name",
                "price",
                "billing_cycle",
                "status",
                "notes",
            )
        }
        result["client_name"] = sub.client.name if sub.client else None
        for field in ("start_date", "renewal_date", "created_at", "cancelled_at"):
            value = getattr(sub, field)
            result[field] = value.isoformat() + "Z" if value else None
        return result

    def list_visible(self, *, client_id=None, status=None):
        query = (
            self._query(Subscription)
            .options(joinedload(Subscription.client))
            .filter(
                Subscription.client.has(owned_record_filter(Client, self.principal))
            )
        )
        if client_id is not None:
            if (
                isinstance(client_id, bool)
                or not str(client_id).isdigit()
                or int(client_id) < 1
            ):
                raise ValueError("Invalid client ID")
            query = query.filter(Subscription.client_id == int(client_id))
        if status:
            if status not in SUBSCRIPTION_STATUSES:
                raise ValueError("Invalid subscription status")
            query = query.filter(Subscription.status == status)
        subs = query.order_by(Subscription.renewal_date.asc()).all()
        return {
            "subscriptions": [self._serialize(sub) for sub in subs],
            "total": len(subs),
        }

    def detail(self, sub_id):
        return self._serialize(self._get(sub_id))

    def create(self, data: SubscriptionCreateSchema):
        if not isinstance(data, SubscriptionCreateSchema):
            raise TypeError("Validated subscription data required")
        client = self._require_record(Client, data.client_id)
        fields = data.model_dump()
        fields["renewal_date"] = data.renewal_date or compute_renewal_date(
            data.start_date, data.billing_cycle
        )
        sub = Subscription(
            **fields,
            tenant_id=self.principal.tenant_id,
            created_by=self.principal.user_id,
            created_at=datetime.utcnow(),
        )
        sub.client = client
        self.session.add(sub)
        self.session.flush()
        return self._serialize(sub)

    def update(self, sub_id, data: SubscriptionUpdateSchema):
        if not isinstance(data, SubscriptionUpdateSchema):
            raise TypeError("Validated subscription data required")
        sub = self._get(sub_id)
        fields = data.model_dump(exclude_unset=True)
        for field in ("plan_name", "price", "billing_cycle", "start_date", "status"):
            if field in fields and fields[field] is None:
                raise ValueError(f"{field} cannot be null")
        if (
            "billing_cycle" in fields or "start_date" in fields
        ) and "renewal_date" not in fields:
            fields["renewal_date"] = compute_renewal_date(
                fields.get("start_date", sub.start_date),
                fields.get("billing_cycle", sub.billing_cycle),
            )
        if fields.get("status") == "cancelled" and sub.cancelled_at is None:
            fields["cancelled_at"] = datetime.utcnow()
        elif fields.get("status") in ("active", "paused"):
            fields["cancelled_at"] = None
        for field, value in fields.items():
            setattr(sub, field, value)
        sub.updated_by = self.principal.user_id
        sub.updated_at = datetime.utcnow()
        self.session.flush()
        return self._serialize(sub)

    def delete(self, sub_id):
        self.session.delete(self._get(sub_id))
        self.session.flush()

    def renew(self, sub_id):
        sub = self._get(sub_id)
        sub.renewal_date = compute_renewal_date(
            sub.renewal_date or sub.start_date, sub.billing_cycle
        )
        if sub.status != "active":
            sub.status = "active"
            sub.cancelled_at = None
        sub.updated_by = self.principal.user_id
        sub.updated_at = datetime.utcnow()
        self.session.flush()
        return self._serialize(sub)
