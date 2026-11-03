"""Capability-bound UI decisions; the host must keep metadata out of model context.

This does not attest human clicks from arbitrary MCP clients. The trusted host
isolates component metadata and app-only tools from the model.
"""
from itsdangerous import URLSafeTimedSerializer, BadSignature
from app.models import User, Tenant
from app.services.ai_actions import payload_hash, ActionConflict

REVIEW_SECONDS = 300


class ReviewResult(dict):
    """Receipt with separately transported component metadata."""
    def __init__(self, receipt, metadata):
        super().__init__(receipt)
        self.component_metadata = metadata


class ActionReview:
    def __init__(self, secret):
        self.signer = URLSafeTimedSerializer(secret, salt='ai-action-component-v1')

    def present(self, service, connection_id, receipt):
        row = service.action(receipt['action_id'], connection_id=connection_id)
        receipt = service.result(row)
        meta = {}
        if receipt['status'] == 'pending':
            user = service.session.query(User).filter_by(id=service.principal.user_id, tenant_id=service.principal.tenant_id).one()
            tenant = service.session.query(Tenant).filter_by(id=service.principal.tenant_id).one()
            meta['pathsix/review'] = {'fields': row.payload, 'account': user.email, 'company': tenant.name,
                'intent': self.signer.dumps(self.binding(service, row))}
        return ReviewResult(receipt, meta)

    @staticmethod
    def binding(service, row):
        return {'id': row.id, 'user': service.principal.user_id,
                'tenant': service.principal.tenant_id, 'connection': row.connection_id,
                'hash': payload_hash(row.payload)}

    def decide(self, service, connection_id, args):
        if (set(args) != {'action_id', 'intent', 'approved'}
                or type(args['approved']) is not bool
                or not isinstance(args['intent'], str) or len(args['intent']) > 2048):
            raise ValueError('Explicit review and decision required')
        row = service.action(args['action_id'], connection_id=connection_id)
        try:
            intent = self.signer.loads(args['intent'], max_age=REVIEW_SECONDS)
        except BadSignature:
            raise ActionConflict('Refresh the review before deciding') from None
        if intent != self.binding(service, row):
            raise PermissionError('Review does not match this action')
        return service.decide(row.id, args['approved'], intent['hash'])
