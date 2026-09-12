"""Account operations inherit active client access; transactions belong to callers."""

from datetime import datetime
from sqlalchemy.orm import joinedload
from app.constants import ACCOUNT_STATUS_OPTIONS
from app.models import Account, Client, ActivityLog, ActivityType
from app.schemas.accounts import AccountCreateSchema, AccountUpdateSchema
from app.services.base import TenantService
from app.services.access import owned_record_filter
from app.services.errors import RecordNotFound


class AccountService(TenantService):
    def _get(self, account_id):
        account = (
            self._query(Account)
            .options(joinedload(Account.client))
            .filter(Account.id == account_id)
            .first()
        )
        if account is None:
            raise RecordNotFound("Account not found")
        self._require_record(Client, account.client_id)
        return account

    @staticmethod
    def _serialize(account, *, client=False):
        result = {
            field: getattr(account, field)
            for field in (
                "id",
                "client_id",
                "account_number",
                "account_name",
                "status",
                "notes",
            )
        }
        result["opened_on"] = (
            account.opened_on.isoformat() + "Z" if account.opened_on else None
        )
        if client:
            result["client_name"] = account.client.name if account.client else None
        return result

    def list_visible(self):
        accounts = (
            self._query(Account)
            .options(joinedload(Account.client))
            .filter(Account.client.has(owned_record_filter(Client, self.principal)))
            .all()
        )
        return [self._serialize(account, client=True) for account in accounts]

    def detail(self, account_id):
        return self._serialize(self._get(account_id), client=True)

    def record_view(self, account_id):
        account = self._get(account_id)
        self.session.add(
            ActivityLog(
                tenant_id=self.principal.tenant_id,
                user_id=self.principal.user_id,
                action=ActivityType.viewed,
                entity_type="account",
                entity_id=account.id,
                description=f"Viewed account '{account.account_number}'",
            )
        )
        self.session.flush()

    def create(self, data: AccountCreateSchema):
        if not isinstance(data, AccountCreateSchema):
            raise TypeError("Validated account data required")
        self._require_record(Client, data.client_id)
        fields = data.model_dump()
        if fields["status"] not in ACCOUNT_STATUS_OPTIONS:
            fields["status"] = ACCOUNT_STATUS_OPTIONS[0]
        fields["opened_on"] = fields["opened_on"] or datetime.utcnow()
        account = Account(**fields, tenant_id=self.principal.tenant_id)
        self.session.add(account)
        self.session.flush()
        return self._serialize(account)

    def update(self, account_id, data: AccountUpdateSchema):
        if not isinstance(data, AccountUpdateSchema):
            raise TypeError("Validated account data required")
        account = self._get(account_id)
        fields = data.model_dump(exclude_unset=True)
        for field in ("client_id", "account_number"):
            if field in fields and fields[field] is None:
                raise ValueError(f"{field} cannot be null")
        self._require_record(Client, fields.get("client_id", account.client_id))
        if "status" in fields and fields["status"] not in ACCOUNT_STATUS_OPTIONS:
            del fields["status"]
        if "opened_on" in fields and fields["opened_on"] is None:
            del fields["opened_on"]
        for field, value in fields.items():
            setattr(account, field, value)
        self.session.flush()
        # Keep subsequent detail/list calls accurate within the same transaction.
        if "client_id" in fields:
            self.session.expire(account, ["client"])
        return self._serialize(account)

    def delete(self, account_id):
        self.session.delete(self._get(account_id))
        self.session.flush()
