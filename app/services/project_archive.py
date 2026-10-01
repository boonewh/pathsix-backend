"""Owner-only, reversible ASFI Project archive. Caller owns the transaction."""
from datetime import datetime
from hashlib import sha256
import json

from itsdangerous import URLSafeTimedSerializer, BadSignature
from sqlalchemy import update

from app.models import Project, Interaction, Client, Lead
from app.utils.project_archive_access import ARCHIVE_TENANT_ID, private_session

CUTOFF = datetime(2026, 5, 1)


def serialize(record):
    def value(v):
        if isinstance(v, datetime):
            return v.isoformat() + 'Z'
        return v.value if hasattr(v, 'value') else v
    return {column.name: value(getattr(record, column.name)) for column in record.__table__.columns}


class ArchiveConflict(ValueError):
    pass


class ProjectArchiveService:
    def __init__(self, session, actor_id, secret):
        self.session = private_session(session)
        self.actor_id = actor_id
        self.signer = URLSafeTimedSerializer(secret, salt='asfi-project-archive-v1')

    def projects(self):
        return self.session.query(Project).filter(Project.tenant_id == ARCHIVE_TENANT_ID)

    def candidates(self, lock=False):
        q = self.projects().filter(Project.created_at < CUTOFF, Project.archived_at.is_(None)).order_by(Project.id)
        return (q.with_for_update() if lock else q).all()

    @staticmethod
    def fingerprint(projects):
        return sha256(json.dumps([serialize(p) for p in projects], sort_keys=True).encode()).hexdigest()

    def preview(self):
        records = self.candidates()
        return {'tenant_id': ARCHIVE_TENANT_ID, 'cutoff': CUTOFF.isoformat() + 'Z',
                'total': len(records), 'in_trash': sum(p.deleted_at is not None for p in records),
                'projects': [serialize(p) for p in records],
                'preview_token': self.signer.dumps({'actor': self.actor_id, 'fingerprint': self.fingerprint(records)})}

    def archive(self, token, confirmation):
        if not isinstance(token, str):
            raise ArchiveConflict('Preview expired or invalid. Review a fresh preview.')
        try:
            preview = self.signer.loads(token, max_age=900)
        except (BadSignature, TypeError):
            raise ArchiveConflict('Preview expired or invalid. Review a fresh preview.')
        records = self.candidates(lock=True)
        if (preview.get('actor') != self.actor_id
                or preview.get('fingerprint') != self.fingerprint(records)):
            raise ArchiveConflict('Projects changed since the preview. Review a fresh preview.')
        if not records or confirmation != f'ARCHIVE {len(records)}':
            raise ValueError('Type the exact archive confirmation shown in the preview.')
        now = datetime.utcnow()
        for p in records:
            history = [*(p.archive_history or []), {'action': 'archived', 'at': now.isoformat() + 'Z', 'by': self.actor_id}]
            # Preserve every business field, including last edit time and Trash state.
            self.session.execute(update(Project).where(Project.id == p.id, Project.tenant_id == ARCHIVE_TENANT_ID)
                .values(archived_at=now, archived_by=self.actor_id, archive_history=history, updated_at=Project.updated_at))
        return {'archived_ids': [p.id for p in records], 'total': len(records)}

    def listing(self, page=1, per_page=25, search=''):
        q = self.projects().filter(Project.archived_at.isnot(None))
        if search:
            q = q.filter(Project.project_name.ilike('%' + search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%', escape='\\'))
        total = q.count()
        records = q.order_by(Project.archived_at.desc(), Project.id).offset((page-1)*per_page).limit(per_page).all()
        return {'projects': [serialize(p) for p in records], 'total': total, 'page': page, 'per_page': per_page}

    def detail(self, project_id):
        p = self.projects().filter(Project.id == project_id, Project.archived_at.isnot(None)).first()
        if p is None:
            return None
        result = serialize(p)
        # Explicit tenant predicates apply even to historical/malformed links.
        for model, field, key in [(Client, p.client_id, 'account_name'), (Lead, p.lead_id, 'lead_name')]:
            parent = self.session.query(model).filter(model.id == field, model.tenant_id == ARCHIVE_TENANT_ID).first() if field else None
            result[key] = parent.name if parent else None
        result['interactions'] = [serialize(i) for i in self.session.query(Interaction).filter(
            Interaction.tenant_id == ARCHIVE_TENANT_ID, Interaction.project_id == p.id).order_by(Interaction.contact_date.desc(), Interaction.id).all()]
        return result

    def restore(self, project_id):
        p = self.projects().filter(Project.id == project_id, Project.archived_at.isnot(None)).with_for_update().first()
        if p is None:
            return None
        history = [*(p.archive_history or []), {'action': 'restored', 'at': datetime.utcnow().isoformat() + 'Z', 'by': self.actor_id}]
        self.session.execute(update(Project).where(Project.id == p.id, Project.tenant_id == ARCHIVE_TENANT_ID)
            .values(archived_at=None, archived_by=None, archive_history=history, updated_at=Project.updated_at))
        return {'id': p.id, 'restored_to': 'trash' if p.deleted_at else 'projects'}
