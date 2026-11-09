"""Bounded delegated reads of contacts, projects, interactions and the schedule.

Every query applies the CRM's existing access rules (owned_record_filter,
project_access_filter, interaction_access_filter), excludes deleted and archived
records, and truncates text at the database boundary. Interaction dates are the
user's local wall-clock times (the web app sends them without an offset), so a
schedule compares them with the user's own calendar dates, never server UTC.
"""
from datetime import date, datetime, timedelta

from sqlalchemy import and_, func, or_

from app.models import Client, Contact, FollowUpStatus, Interaction, Lead, Project
from app.services.access import interaction_access_filter, owned_record_filter, project_access_filter
from app.services.base import TenantService
from app.services.errors import RecordNotFound

MAX_ID = 2147483647
MAX_SCHEDULE_DAYS = 31
SCHEDULE_LIMIT = 100


def _ident(value, *, optional=True):
    if value is None and optional:
        return None
    if type(value) is not int or not 1 <= value <= MAX_ID:
        raise ValueError('Invalid record ID')
    return value


def _page(after_id, limit):
    if type(after_id) is not int or not 0 <= after_id <= MAX_ID or type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('Invalid page')


def _search(column, query):
    if not isinstance(query, str) or len(query) > 100:
        raise ValueError('Invalid search')
    literal = query.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return column.ilike('%' + literal + '%', escape='\\') if literal else None


def _text(column, bound):
    return func.substr(column, 1, bound)


def _when(value):
    return value.isoformat() if value else None


def _paged(rows, limit, key):
    items = [dict(row._mapping) for row in rows[:limit]]
    return {key: items, 'next_after_id': rows[limit - 1].id if len(rows) > limit else None}


class RecordReads(TenantService):
    # Clients ---------------------------------------------------------------
    def client_detail(self, client_id):
        client_id = _ident(client_id, optional=False)
        row = self._query(Client).filter(Client.id == client_id, owned_record_filter(Client, self.principal)).with_entities(
            Client.id, _text(Client.name, 200).label('name'), _text(Client.type, 100).label('type'),
            _text(Client.status, 50).label('status'), _text(Client.contact_person, 100).label('contact_person'),
            _text(Client.contact_title, 100).label('contact_title'), _text(Client.email, 120).label('email'),
            _text(Client.phone, 20).label('phone'), _text(Client.secondary_phone, 20).label('secondary_phone'),
            _text(Client.address, 255).label('address'), _text(Client.city, 100).label('city'),
            _text(Client.state, 100).label('state'), _text(Client.zip, 20).label('zip'),
            _text(Client.notes, 4000).label('notes')).first()
        if row is None:
            raise RecordNotFound('Client not found')
        return {'client': dict(row._mapping)}

    # Contacts --------------------------------------------------------------
    def _contact_access(self):
        p = self.principal
        return or_(
            and_(Contact.lead_id.is_(None), Contact.client.has(owned_record_filter(Client, p))),
            and_(Contact.client_id.is_(None), Contact.lead.has(owned_record_filter(Lead, p))))

    def contacts(self, *, query='', client_id=None, lead_id=None, contact_id=None, after_id=0, limit=20):
        client_id, lead_id, contact_id = _ident(client_id), _ident(lead_id), _ident(contact_id)
        _page(after_id, limit)
        if client_id and lead_id:
            raise ValueError('Choose a client or a lead, not both')
        rows = self._query(Contact).filter(self._contact_access())
        if client_id:
            self._require_parent(Client, client_id)
            rows = rows.filter(Contact.client_id == client_id, Contact.lead_id.is_(None))
        if lead_id:
            self._require_parent(Lead, lead_id)
            rows = rows.filter(Contact.lead_id == lead_id, Contact.client_id.is_(None))
        if contact_id:
            rows = rows.filter(Contact.id == contact_id)
        else:
            rows = rows.filter(Contact.id > after_id)
        match = _search(func.concat(func.coalesce(Contact.first_name, ''), ' ', func.coalesce(Contact.last_name, ''),
                                    ' ', func.coalesce(Contact.email, '')), query)
        if match is not None:
            rows = rows.filter(match)
        columns = [Contact.id, _text(Contact.first_name, 100).label('first_name'),
                   _text(Contact.last_name, 100).label('last_name'), _text(Contact.title, 100).label('title'),
                   _text(Contact.email, 120).label('email'), _text(Contact.phone, 20).label('phone'),
                   _text(Contact.phone_label, 20).label('phone_label'), Contact.client_id, Contact.lead_id]
        if contact_id:
            columns += [_text(Contact.secondary_phone, 20).label('secondary_phone'),
                        _text(Contact.secondary_phone_label, 20).label('secondary_phone_label'),
                        _text(Contact.notes, 4000).label('notes')]
        result = rows.with_entities(*columns).order_by(Contact.id).limit(limit + 1).all()
        if contact_id and not result:
            raise RecordNotFound('Contact not found')
        page = _paged(result, limit, 'contacts')
        self._name_parents(page['contacts'])
        return page

    def _require_parent(self, model, ident):
        if self._query(model).filter(model.id == ident, owned_record_filter(model, self.principal)).first() is None:
            raise RecordNotFound('Record not found')

    def _name_parents(self, items):
        """Attach a parent's name only when the caller can access that parent.

        A record can be visible while its parent is not (for example a project
        assigned to the caller under someone else's client); then the name is
        omitted rather than disclosed.
        """
        names = {}
        access = {'client': owned_record_filter(Client, self.principal), 'lead': owned_record_filter(Lead, self.principal),
                  'project': and_(Project.deleted_at.is_(None), Project.archived_at.is_(None),
                                  project_access_filter(self.principal))}
        for kind, model in (('client', Client), ('lead', Lead), ('project', Project)):
            ids = {item[kind + '_id'] for item in items if item.get(kind + '_id')}
            if ids:
                label = Project.project_name if model is Project else model.name
                names[kind] = dict(self._query(model).filter(model.id.in_(ids), access[kind]).with_entities(
                    model.id, _text(label, 200)).all())
        for item in items:
            for kind in ('client', 'lead', 'project'):
                if item.get(kind + '_id'):
                    item[kind + '_name'] = names.get(kind, {}).get(item[kind + '_id'])

    # Projects --------------------------------------------------------------
    def projects(self, *, query='', status=None, project_id=None, after_id=0, limit=20):
        project_id = _ident(project_id)
        _page(after_id, limit)
        if status is not None and status not in ('pending', 'won', 'lost'):
            raise ValueError('Invalid project status')
        rows = self._query(Project).filter(Project.deleted_at.is_(None), Project.archived_at.is_(None),
                                           project_access_filter(self.principal))
        rows = rows.filter(Project.id == project_id) if project_id else rows.filter(Project.id > after_id)
        if status:
            rows = rows.filter(Project.project_status == status)
        match = _search(Project.project_name, query)
        if match is not None:
            rows = rows.filter(match)
        columns = [Project.id, _text(Project.project_name, 255).label('project_name'),
                   Project.project_status, _text(Project.type, 50).label('type'), Project.client_id,
                   Project.lead_id, Project.project_start, Project.project_end, Project.project_worth,
                   _text(Project.value_type, 20).label('value_type')]
        if project_id:
            columns += [_text(Project.project_description, 4000).label('project_description'),
                        _text(Project.primary_contact_name, 100).label('primary_contact_name'),
                        _text(Project.primary_contact_title, 100).label('primary_contact_title'),
                        _text(Project.primary_contact_email, 120).label('primary_contact_email'),
                        _text(Project.primary_contact_phone, 20).label('primary_contact_phone'),
                        _text(Project.notes, 4000).label('notes')]
        result = rows.with_entities(*columns).order_by(Project.id).limit(limit + 1).all()
        if project_id and not result:
            raise RecordNotFound('Project not found')
        page = _paged(result, limit, 'projects')
        for item in page['projects']:
            item['project_start'], item['project_end'] = _when(item['project_start']), _when(item['project_end'])
        self._name_parents(page['projects'])
        return page

    # Interactions ----------------------------------------------------------
    def _interactions(self):
        not_archived = or_(Interaction.project_id.is_(None), Interaction.project.has(Project.archived_at.is_(None)))
        return self._query(Interaction).filter(interaction_access_filter(self.principal), not_archived)

    @staticmethod
    def _interaction_columns(notes=2000):
        return [Interaction.id, Interaction.contact_date, Interaction.follow_up, Interaction.followup_status,
                _text(Interaction.summary, 255).label('summary'), _text(Interaction.outcome, 255).label('outcome'),
                _text(Interaction.contact_person, 100).label('contact_person'),
                _text(Interaction.notes, notes).label('notes'),
                Interaction.client_id, Interaction.lead_id, Interaction.project_id]

    def _serialize_interactions(self, rows):
        items = [dict(row._mapping) for row in rows]
        for item in items:
            item['contact_date'], item['follow_up'] = _when(item['contact_date']), _when(item['follow_up'])
            status = item['followup_status']
            item['followup_status'] = status.value if isinstance(status, FollowUpStatus) else status
        self._name_parents(items)
        return items

    def interactions(self, *, client_id=None, lead_id=None, project_id=None, after_id=0, limit=20):
        client_id, lead_id, project_id = _ident(client_id), _ident(lead_id), _ident(project_id)
        _page(after_id, limit)
        if sum(x is not None for x in (client_id, lead_id, project_id)) != 1:
            raise ValueError('Choose one client, lead or project')
        rows = self._interactions().filter(Interaction.id > after_id)
        if client_id:
            rows = rows.filter(Interaction.client_id == client_id)
        elif lead_id:
            rows = rows.filter(Interaction.lead_id == lead_id)
        else:
            rows = rows.filter(Interaction.project_id == project_id)
        result = rows.with_entities(*self._interaction_columns()).order_by(Interaction.id).limit(limit + 1).all()
        return {'interactions': self._serialize_interactions(result[:limit]),
                'next_after_id': result[limit - 1].id if len(result) > limit else None}

    # Schedule --------------------------------------------------------------
    def schedule(self, *, start_date, end_date):
        try:
            start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        except (TypeError, ValueError):
            raise ValueError('Use dates like 2026-11-09') from None
        if end < start or (end - start).days >= MAX_SCHEDULE_DAYS:
            raise ValueError(f'Choose a range of 1 to {MAX_SCHEDULE_DAYS} days')
        # Local wall-clock bounds: the user's own calendar days.
        first, after = datetime.combine(start, datetime.min.time()), datetime.combine(end + timedelta(days=1), datetime.min.time())
        open_follow_up = Interaction.followup_status != FollowUpStatus.completed
        columns = self._interaction_columns(notes=500)
        follow_ups = self._interactions().filter(Interaction.follow_up >= first, Interaction.follow_up < after,
                                                 open_follow_up).with_entities(*columns).order_by(
            Interaction.follow_up, Interaction.id).limit(SCHEDULE_LIMIT + 1).all()
        contacts = self._interactions().filter(Interaction.contact_date >= first, Interaction.contact_date < after
                                               ).with_entities(*columns).order_by(
            Interaction.contact_date, Interaction.id).limit(SCHEDULE_LIMIT + 1).all()
        overdue = self._interactions().filter(Interaction.follow_up < first, open_follow_up).with_entities(
            *columns).order_by(Interaction.follow_up, Interaction.id).limit(SCHEDULE_LIMIT + 1).all()
        return {'start_date': start.isoformat(), 'end_date': end.isoformat(),
                'follow_ups': self._serialize_interactions(follow_ups[:SCHEDULE_LIMIT]),
                'contacts': self._serialize_interactions(contacts[:SCHEDULE_LIMIT]),
                'overdue_follow_ups': self._serialize_interactions(overdue[:SCHEDULE_LIMIT]),
                'truncated': any(len(group) > SCHEDULE_LIMIT for group in (follow_ups, contacts, overdue))}
