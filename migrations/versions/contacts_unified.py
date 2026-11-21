"""Store every person once, in contacts.

Clients and leads kept their main person in their own columns, projects kept a
copy, and only extra people lived in contacts. This copies each of those people
into contacts: a client's or lead's main person becomes its primary contact, a
project points at one contact (one of its client's or lead's, or its own when
it stands alone), and people stranded on converted, deleted leads move to the
client the lead became.

The old columns are copied, not dropped. The application stops reading them,
so they stay as a frozen backup until a later migration removes them.
Existing contacts are never deleted; a matching existing person (same record,
same email or name) is promoted or linked instead of duplicated.
"""
from alembic import op
from sqlalchemy import text

revision = 'contacts_unified'
# Repointed after the mobile migrations (owner decision, November 20, 2026:
# contacts-cleanup merged into the mobile branch, which carries both to production).
down_revision = 'mobile_record_refs'
branch_labels = depends_on = None

# Frozen copies: migrations must not follow later model changes.
PERSON_COLUMNS = ('contact_person', 'contact_title', 'email', 'phone', 'phone_label',
                  'secondary_phone', 'secondary_phone_label')
PROJECT_COLUMNS = ('primary_contact_name', 'primary_contact_title', 'primary_contact_email',
                   'primary_contact_phone', 'primary_contact_phone_label')


def split_name(value):
    words = ' '.join((value or '').split())
    if not words:
        return None, None
    first, _, last = words.rpartition(' ')
    return (first, last) if first else (last, None)


def clean(value):
    value = (value or '').strip() if isinstance(value, str) else value
    return value or None


def find_match(candidates, email, name):
    email = (email or '').strip().lower()
    name = ' '.join((name or '').split()).lower()
    for contact in candidates:
        if email and (contact['email'] or '').strip().lower() == email:
            return contact
    for contact in candidates:
        full = ' '.join(p for p in (contact['first_name'], contact['last_name']) if p).lower()
        if name and full == name:
            return contact
    return None


def add_schema(connection, q, schema):
    t = lambda name: f'{q(schema)}.{q(name)}'
    connection.execute(text(f'ALTER TABLE {t("contacts")} ADD COLUMN project_id integer, '
                            f'ADD COLUMN is_primary boolean NOT NULL DEFAULT false'))
    connection.execute(text(f'ALTER TABLE {t("projects")} ADD COLUMN primary_contact_id integer'))
    connection.execute(text(f'ALTER TABLE {t("contacts")} ADD CONSTRAINT contacts_project_id_fkey '
                            f'FOREIGN KEY (project_id) REFERENCES {t("projects")} (id)'))
    connection.execute(text(f'ALTER TABLE {t("contacts")} ADD CONSTRAINT fk_contacts_project_id_same_tenant '
                            f'FOREIGN KEY (tenant_id, project_id) REFERENCES {t("projects")} (tenant_id, id)'))
    connection.execute(text(f'ALTER TABLE {t("contacts")} ADD CONSTRAINT uq_contacts_tenant_id_id UNIQUE (tenant_id, id)'))
    connection.execute(text(f'ALTER TABLE {t("projects")} ADD CONSTRAINT projects_primary_contact_id_fkey '
                            f'FOREIGN KEY (primary_contact_id) REFERENCES {t("contacts")} (id) ON DELETE SET NULL'))
    # SET NULL on the contact column only: tenant_id is never cleared.
    connection.execute(text(f'ALTER TABLE {t("projects")} ADD CONSTRAINT fk_projects_primary_contact_id_same_tenant '
                            f'FOREIGN KEY (tenant_id, primary_contact_id) REFERENCES {t("contacts")} (tenant_id, id) '
                            f'ON DELETE SET NULL (primary_contact_id)'))
    connection.execute(text(f'ALTER TABLE {t("contacts")} DROP CONSTRAINT ck_contacts_one_parent'))
    connection.execute(text(f'ALTER TABLE {t("contacts")} ADD CONSTRAINT ck_contacts_one_parent '
                            f'CHECK (num_nonnulls(client_id, lead_id, project_id) = 1)'))
    for key in ('client_id', 'lead_id', 'project_id'):
        connection.execute(text(f'CREATE UNIQUE INDEX {q("uq_contacts_primary_" + key)} '
                                f'ON {t("contacts")} ({q(key)}) WHERE is_primary'))
    connection.execute(text(f'CREATE INDEX ix_contacts_project_id ON {t("contacts")} (project_id)'))


def contacts_of(connection, t, key, owner_id):
    # Live contacts only: after the mobile migrations a contact may be a tombstone
    # (mobile_tombstones), which must never be promoted or linked as someone's person.
    return [dict(row._mapping) for row in connection.execute(text(
        f'SELECT id, first_name, last_name, title, email, phone, phone_label, secondary_phone, '
        f'secondary_phone_label, is_primary FROM {t("contacts")} WHERE {key} = :owner '
        f'AND deleted_at IS NULL ORDER BY id'),
        {'owner': owner_id})]


def insert_contact(connection, t, tenant_id, key, owner_id, values, is_primary):
    first, last = split_name(values.get('name'))
    return connection.execute(text(
        f'INSERT INTO {t("contacts")} (tenant_id, {key}, is_primary, first_name, last_name, title, email, phone, '
        f'phone_label, secondary_phone, secondary_phone_label, created_at) VALUES (:tenant, :owner, :primary, '
        f':first, :last, :title, :email, :phone, :phone_label, :secondary_phone, :secondary_phone_label, '
        f"(now() AT TIME ZONE 'utc')) RETURNING id"),
        {'tenant': tenant_id, 'owner': owner_id, 'primary': is_primary, 'first': first, 'last': last,
         'title': values.get('title'), 'email': values.get('email'), 'phone': values.get('phone'),
         'phone_label': values.get('phone_label') or ('work' if values.get('phone') else None),
         'secondary_phone': values.get('secondary_phone'),
         'secondary_phone_label': values.get('secondary_phone_label')}).scalar_one()


def fill_gaps(connection, t, contact, values):
    """Copy details the existing person lacks; never overwrite what they have."""
    updates = {}
    first, last = split_name(values.get('name'))
    for column, value in (('first_name', first), ('last_name', last), ('title', values.get('title')),
                          ('email', values.get('email')), ('phone', values.get('phone')),
                          ('secondary_phone', values.get('secondary_phone')),
                          ('secondary_phone_label', values.get('secondary_phone_label'))):
        if value and not contact[column]:
            updates[column] = value
    if values.get('phone') and 'phone' in updates and values.get('phone_label'):
        updates['phone_label'] = values['phone_label']
    if updates:
        assignments = ', '.join(f'{column} = :{column}' for column in updates)
        connection.execute(text(f'UPDATE {t("contacts")} SET {assignments} WHERE id = :id'),
                           {**updates, 'id': contact['id']})


def backfill(connection, q, schema):
    t = lambda name: f'{q(schema)}.{q(name)}'
    counts = {'moved': 0, 'promoted': 0, 'created': 0, 'project_linked': 0, 'project_created': 0}
    # 1. People left on a converted lead that was then deleted belong to the client.
    counts['moved'] = connection.execute(text(
        f'UPDATE {t("contacts")} c SET client_id = cl.id, lead_id = NULL '
        f'FROM (SELECT DISTINCT ON (source_lead_id) id, source_lead_id, tenant_id FROM {t("clients")} '
        f'      WHERE source_lead_id IS NOT NULL ORDER BY source_lead_id, id) cl, {t("leads")} l '
        f'WHERE c.lead_id = cl.source_lead_id AND c.tenant_id = cl.tenant_id '
        f'AND l.id = cl.source_lead_id AND l.deleted_at IS NOT NULL')).rowcount
    # 2. Each client's and lead's own person becomes its primary contact.
    for table, key in (('clients', 'client_id'), ('leads', 'lead_id')):
        rows = connection.execute(text(
            f'SELECT id, tenant_id, {", ".join(PERSON_COLUMNS)} FROM {t(table)} ORDER BY id')).all()
        for row in rows:
            r = row._mapping
            values = {'name': clean(r['contact_person']), 'title': clean(r['contact_title']),
                      'email': clean(r['email']), 'phone': clean(r['phone']), 'phone_label': clean(r['phone_label']),
                      'secondary_phone': clean(r['secondary_phone']),
                      'secondary_phone_label': clean(r['secondary_phone_label'])}
            if not any(values[k] for k in ('name', 'title', 'email', 'phone', 'secondary_phone')):
                continue
            match = find_match(contacts_of(connection, t, key, r['id']), values['email'], values['name'])
            if match is not None:
                fill_gaps(connection, t, match, values)
                connection.execute(text(f'UPDATE {t("contacts")} SET is_primary = true WHERE id = :id'),
                                   {'id': match['id']})
                counts['promoted'] += 1
            else:
                insert_contact(connection, t, r['tenant_id'], key, r['id'], values, True)
                counts['created'] += 1
    # 3. A project points at its person: its client's or lead's, or its own.
    rows = connection.execute(text(
        f'SELECT id, tenant_id, client_id, lead_id, {", ".join(PROJECT_COLUMNS)} FROM {t("projects")} ORDER BY id'
    )).all()
    for row in rows:
        r = row._mapping
        values = {'name': clean(r['primary_contact_name']), 'title': clean(r['primary_contact_title']),
                  'email': clean(r['primary_contact_email']), 'phone': clean(r['primary_contact_phone']),
                  'phone_label': clean(r['primary_contact_phone_label'])}
        if not any(values[k] for k in ('name', 'title', 'email', 'phone')):
            continue
        if r['client_id'] is not None or r['lead_id'] is not None:
            key, owner = ('client_id', r['client_id']) if r['client_id'] is not None else ('lead_id', r['lead_id'])
            match = find_match(contacts_of(connection, t, key, owner), values['email'], values['name'])
            if match is not None:
                contact_id = match['id']
                counts['project_linked'] += 1
            else:
                contact_id = insert_contact(connection, t, r['tenant_id'], key, owner, values, False)
                counts['project_created'] += 1
        else:
            contact_id = insert_contact(connection, t, r['tenant_id'], 'project_id', r['id'], values, True)
            counts['project_created'] += 1
        connection.execute(text(f'UPDATE {t("projects")} SET primary_contact_id = :contact WHERE id = :id'),
                           {'contact': contact_id, 'id': r['id']})
    return counts


def reconcile(connection, schema):
    if connection.dialect.name != 'postgresql':
        raise RuntimeError('The contacts migration requires PostgreSQL')
    q = connection.dialect.identifier_preparer.quote
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text("SET LOCAL statement_timeout='120s'"))
    # Fail loudly instead of copying a row-security-filtered subset.
    connection.execute(text('SET LOCAL row_security = off'))
    add_schema(connection, q, schema)
    return backfill(connection, q, schema)


def upgrade():
    connection = op.get_bind()
    counts = reconcile(connection, connection.execute(text('SELECT current_schema()')).scalar_one())
    print('contacts_unified: ' + ', '.join(f'{key}={value}' for key, value in counts.items()))


def downgrade():
    # The old columns were never changed by this migration, but edits made since
    # live only in contacts. Restoring needs a reviewed copy-back, not a drop.
    raise RuntimeError('Restoring per-record contact columns requires a reviewed migration')
