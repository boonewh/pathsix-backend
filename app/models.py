from sqlalchemy import Column, Integer, BigInteger, String, DateTime, Text, Float, ForeignKey, Table, Boolean, Uuid, FetchedValue
from sqlalchemy import func, select, false
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship, backref
from sqlalchemy.orm.attributes import flag_dirty
from datetime import datetime
from uuid import uuid4
from app.database import Base
from sqlalchemy import Enum, Index, UniqueConstraint, JSON, ForeignKeyConstraint, CheckConstraint, DDL, event, text
import enum

# Association table for many-to-many User ↔ Role
user_roles = Table(
    'user_roles',
    Base.metadata,
    Column('user_id', Integer, ForeignKey('users.id')),
    Column('role_id', Integer, ForeignKey('roles.id')),
)

class SyncMarked:
    """Change-feed markers, maintained by database triggers (app/utils/sync_markers.py)."""
    sync_change_xid = Column(BigInteger, nullable=False, server_default=text('0'), server_onupdate=FetchedValue())
    sync_access_xid = Column(BigInteger, nullable=False, server_default=text('0'), server_onupdate=FetchedValue())


class FollowUpStatus(enum.Enum):
    pending = "pending"
    contacted = "contacted"
    completed = "completed"
    rescheduled = "rescheduled"


class Tenant(Base):
    """
    Multi-tenant configuration table.

    Each tenant has their own branding, labels, statuses, and feature toggles.
    The config JSON blob stores all customizable settings that the frontend needs.
    """
    __tablename__ = 'tenants'

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)  # Display name: "ASFI", "Acme Corp"
    slug = Column(String(50), unique=True, nullable=False, index=True)  # URL-safe: "asfi", "acme"
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # JSON config blob - matches frontend CRMConfig interface
    # Structure: { branding, labels, leads, businessTypes, regional, features, defaults }
    config = Column(JSON, nullable=False, default=dict)

    # Relationships
    users = relationship("User", back_populates="tenant")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "is_active": self.is_active,
            "config": self.config,
        }

    def __repr__(self):
        return f"<Tenant {self.slug}>"


class User(Base):
    __tablename__ = 'users'
    __table_args__ = (UniqueConstraint('tenant_id', 'id', name='uq_users_tenant_id_id'),)
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False, index=True)
    email = Column(String(120), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    roles = relationship("Role", secondary=user_roles, back_populates="users")
    tenant = relationship("Tenant", back_populates="users")

    def __repr__(self):
        return f"<User {self.email}>"


class Role(Base):
    __tablename__ = 'roles'
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)

    users = relationship("User", secondary=user_roles, back_populates="roles")

    def __repr__(self):
        return f"<Role {self.name}>"


class AIClient(Base):
    """Client catalog: operator rows plus self-registered public clients.

    The web runtime has read access only; self-registration goes through the
    crm_register_ai_client database function. Only operator clients may use the
    inline review card, whose host must keep result metadata from the model.
    """
    __tablename__ = 'ai_clients'
    __table_args__ = (
        CheckConstraint("registration IN ('operator','cimd','dcr')", name='ck_ai_clients_registration'),
        CheckConstraint("NOT inline_review OR registration = 'operator'", name='ck_ai_clients_inline_operator'),
        Index('ix_ai_clients_registration_created', 'registration', 'created_at'),
    )
    id = Column(String(200), primary_key=True)
    name = Column(String(100), nullable=False)
    is_active = Column(Boolean, nullable=False, default=False)
    allowed_scopes = Column(JSON, nullable=False, default=list)
    redirect_uris = Column(JSON, nullable=False, default=list)
    oauth_enabled = Column(Boolean, nullable=False, default=False)
    registration = Column(String(10), nullable=False, default='operator')
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    metadata_fetched_at = Column(DateTime)
    inline_review = Column(Boolean, nullable=False, default=False)


class AIConnection(Base):
    """An owner's consent record, never a bearer credential or web identity."""
    __tablename__ = 'ai_connections'
    __table_args__ = (
        UniqueConstraint('tenant_id', 'user_id', 'id', name='uq_ai_connections_owner_id'),
        ForeignKeyConstraint(['tenant_id', 'user_id'], ['users.tenant_id', 'users.id'],
                             name='fk_ai_connections_owner_same_tenant'),
        CheckConstraint('expires_at > created_at', name='ck_ai_connections_expiry'),
        CheckConstraint('active_until > created_at AND active_until <= expires_at', name='ck_ai_connections_active_until'),
        Index('ix_ai_connections_owner_created', 'tenant_id', 'user_id', 'created_at', 'id'),
    )
    id = Column(String(36), primary_key=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False)
    user_id = Column(Integer, nullable=False)
    client_id = Column(String(200), ForeignKey('ai_clients.id'), nullable=False)
    resource = Column(String(500), nullable=False)
    scopes = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    # expires_at is the immutable absolute limit; active_until slides forward
    # with use (token exchange) and never passes expires_at.
    expires_at = Column(DateTime, nullable=False)
    active_until = Column(DateTime, nullable=False,
                          default=lambda context: context.get_current_parameters()['expires_at'])
    revoked_at = Column(DateTime)
    last_used_at = Column(DateTime)


class OAuthCredential(Base):
    """Hashed, single-purpose credentials; raw secrets are returned only once."""
    __tablename__ = 'oauth_credentials'
    __table_args__ = (
        ForeignKeyConstraint(['tenant_id', 'user_id', 'connection_id'],
                             ['ai_connections.tenant_id', 'ai_connections.user_id', 'ai_connections.id'],
                             name='fk_oauth_credentials_owner_grant'),
        CheckConstraint("kind IN ('code','access','refresh')", name='ck_oauth_credential_kind'),
        Index('ix_oauth_credentials_owner_grant', 'tenant_id', 'user_id', 'connection_id', 'created_at'),
    )
    token_hash = Column(String(64), primary_key=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False)
    user_id = Column(Integer, nullable=False)
    connection_id = Column(String(36), nullable=False)
    kind = Column(String(10), nullable=False)
    scopes = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime)
    redirect_uri = Column(String(1000))
    code_challenge = Column(String(43))
    intent_hash = Column(String(64), unique=True)


class AIToolAudit(Base):
    """Append-only invocation metadata; never stores arguments or CRM content."""
    __tablename__ = 'ai_tool_audits'
    __table_args__ = (
        ForeignKeyConstraint(['tenant_id','user_id','connection_id'],
            ['ai_connections.tenant_id','ai_connections.user_id','ai_connections.id'], name='fk_ai_tool_audits_owner_grant'),
        CheckConstraint("outcome IN ('success','not_found','invalid_arguments','forbidden','unknown_tool')", name='ck_ai_tool_audits_outcome'),
        Index('ix_ai_tool_audits_owner_created','tenant_id','user_id','connection_id','created_at'),
    )
    id = Column(String(36),primary_key=True)
    tenant_id = Column(Integer,ForeignKey('tenants.id'),nullable=False)
    user_id = Column(Integer,nullable=False)
    connection_id = Column(String(36),nullable=False)
    tool = Column(String(40),nullable=False)
    outcome = Column(String(30),nullable=False)
    result_count = Column(Integer,nullable=False)
    created_at = Column(DateTime,nullable=False,default=datetime.utcnow)


class AIWriteAction(Base):
    """Immutable proposal and durable receipt; an ID alone never authorizes a write."""
    __tablename__ = 'ai_write_actions'
    __table_args__ = (
        ForeignKeyConstraint(['tenant_id', 'user_id', 'connection_id'],
            ['ai_connections.tenant_id', 'ai_connections.user_id', 'ai_connections.id'],
            name='fk_ai_write_actions_owner_grant'),
        UniqueConstraint('connection_id', 'request_key', name='uq_ai_write_actions_request'),
        CheckConstraint("kind IN ('create_lead','create_contact','create_project','create_interaction','update_lead',"
                        "'update_client','update_contact','update_project','update_interaction')",
                        name='ck_ai_write_actions_kind'),
        CheckConstraint("status IN ('pending','cancelled','committed')", name='ck_ai_write_actions_status'),
        CheckConstraint("(status = 'committed' AND result_id IS NOT NULL AND decided_at IS NOT NULL) OR "
                        "(status = 'cancelled' AND result_id IS NULL AND decided_at IS NOT NULL) OR "
                        "(status = 'pending' AND result_id IS NULL AND decided_at IS NULL)",
                        name='ck_ai_write_actions_result'),
        CheckConstraint('expires_at > created_at', name='ck_ai_write_actions_expiry'),
        Index('ix_ai_write_actions_owner_created', 'tenant_id', 'user_id', 'created_at'),
    )
    id = Column(String(36), primary_key=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False)
    user_id = Column(Integer, nullable=False)
    connection_id = Column(String(36), nullable=False)
    request_key = Column(String(64), nullable=False)
    input_hash = Column(String(64), nullable=False)
    kind = Column(String(30), nullable=False)
    payload = Column(JSON, nullable=False)
    status = Column(String(12), nullable=False, default='pending')
    result_id = Column(Integer)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    decided_at = Column(DateTime)


# The person fields that clients, leads and projects expose, mapped to Contact
# columns. ``None`` is the full name, stored split as first and last name.
CONTACT_COLUMNS = {'contact_person': None, 'contact_title': 'title', 'email': 'email', 'phone': 'phone',
                   'phone_label': 'phone_label', 'secondary_phone': 'secondary_phone',
                   'secondary_phone_label': 'secondary_phone_label'}
PROJECT_CONTACT_COLUMNS = {'primary_contact_name': None, 'primary_contact_title': 'title',
                           'primary_contact_email': 'email', 'primary_contact_phone': 'phone',
                           'primary_contact_phone_label': 'phone_label'}
# Labels alone do not make a person worth keeping.
CONTACT_CONTENT = ('first_name', 'last_name', 'title', 'email', 'phone', 'secondary_phone')


def split_name(value):
    """'Mary Ann Smith' -> ('Mary Ann', 'Smith'); a single word is a first name."""
    words = ' '.join((value or '').split())
    if not words:
        return None, None
    first, _, last = words.rpartition(' ')
    return (first, last) if first else (last, None)


def full_name(first, last):
    return ' '.join(part for part in (first, last) if part) or None


def _contact_value(contact, column):
    if contact is None:
        return None
    return full_name(contact.first_name, contact.last_name) if column is None else getattr(contact, column)


def _set_contact_value(contact, column, value):
    if column is None:
        contact.first_name, contact.last_name = split_name(value)
    else:
        setattr(contact, column, value)


def _primary_field(column):
    def fget(self):
        return _contact_value(self.primary_contact_row, column)

    def fset(self, value):
        contact = self.primary_contact_row
        if contact is None:
            if value in (None, ''):
                return
            contact = Contact(is_primary=True, tenant_id=self.tenant_id)
            self.contacts.append(contact)
        _set_contact_value(contact, column, value)

    def expr(cls):
        value = Contact.display_name if column is None else getattr(Contact, column)
        # Tombstoned contacts are excluded here too: the change feed reads through
        # a Core connection, outside the ORM tombstone filter.
        return (select(value).where(getattr(Contact, cls.contact_parent_key) == cls.id,
                                    Contact.is_primary.is_(True), Contact.deleted_at.is_(None))
                .correlate_except(Contact).scalar_subquery())

    return hybrid_property(fget, fset, expr=expr)


class PrimaryContactFields:
    """Client and lead person fields, read from and written to the primary contact.

    The API and the AI connector keep their contact_person/email/phone shape;
    every person is stored once, in the contacts table.
    """
    contact_parent_key = None

    @property
    def primary_contact_row(self):
        return next((contact for contact in self.contacts if contact.is_primary), None)


for _field, _column in CONTACT_COLUMNS.items():
    setattr(PrimaryContactFields, _field, _primary_field(_column))


class Client(PrimaryContactFields, SyncMarked, Base):
    __tablename__ = 'clients'
    contact_parent_key = 'client_id'
    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(Uuid, nullable=False, unique=True, default=uuid4)  # stable id for offline sync
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False, index=True)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    updated_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    assigned_to = Column(Integer, ForeignKey('users.id'), nullable=True)
    name = Column(String(100), nullable=False)
    # contact_person, contact_title, email, phone and their labels are the
    # primary contact's fields; see PrimaryContactFields. The old columns of the
    # same names remain in the database, frozen, until a later cleanup drops them.
    address = Column(String(255))
    city = Column(String(100))
    state = Column(String(100))
    zip = Column(String(20))
    status = Column(String(50), default='new')
    type = Column(String(50), nullable=True, default="None")
    notes = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)
    source_lead_id = Column(Integer, ForeignKey('leads.id'), nullable=True)
    converted_on = Column(DateTime, nullable=True)

    # Relationships
    assigned_user = relationship("User", foreign_keys=[assigned_to])
    created_by_user = relationship("User", foreign_keys=[created_by])

    def __repr__(self):
        return f"<Client {self.name}>"


class Account(SyncMarked, Base):
    __tablename__ = 'accounts'

    id = Column(Integer, primary_key=True)
    uuid = Column(Uuid, nullable=False, unique=True, default=uuid4)  # stable id for offline sync
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    client_id = Column(Integer, ForeignKey('clients.id'), nullable=False)
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False, index=True)
    account_number = Column(String(100), nullable=False)
    account_name = Column(String(255), nullable=True)
    status = Column(String(50), default="active")
    opened_on = Column(DateTime, default=datetime.utcnow)
    notes = Column(Text)
    # Tombstone: hidden from every read, kept for offline devices (app/utils/tombstones.py).
    deleted_at = Column(DateTime, nullable=True)
    deleted_by = Column(Integer, ForeignKey('users.id'), nullable=True)

    # Unique among live accounts only, so a deleted account's number can be reused.
    __table_args__ = (
        Index('uq_accounts_account_number_live', 'account_number', unique=True,
              postgresql_where=text('deleted_at IS NULL'), sqlite_where=text('deleted_at IS NULL')),
    )

    client = relationship("Client", backref="accounts")

    def __repr__(self):
        return f"<Account {self.account_number}>"


class Lead(PrimaryContactFields, SyncMarked, Base):
    __tablename__ = 'leads'
    contact_parent_key = 'lead_id'
    id = Column(Integer, primary_key=True)
    uuid = Column(Uuid, nullable=False, unique=True, default=uuid4)  # stable id for offline sync
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False, index=True)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    updated_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)
    deleted_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    assigned_to = Column(Integer, ForeignKey('users.id'), nullable=True)
    name = Column(String(100), nullable=False)
    # contact_person, contact_title, email, phone and their labels are the
    # primary contact's fields; see PrimaryContactFields. The old columns of the
    # same names remain in the database, frozen, until a later cleanup drops them.
    address = Column(String(255))
    city = Column(String(100))
    state = Column(String(100))
    zip = Column(String(20))
    type = Column(String(50), nullable=True, default="None")
    notes = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    lead_status = Column(String(20), default="open")  # Tenant-configured; default pipeline: open, qualified, proposal, won, lost
    converted_on = Column(DateTime, nullable=True)
    lead_source = Column(String(50), nullable=True, index=True)  # Website, Referral, Cold Call, Email Campaign, etc.

    assigned_user = relationship("User", foreign_keys=[assigned_to])
    created_by_user = relationship("User", foreign_keys=[created_by])


    def __repr__(self):
        return f"<Lead {self.name}>"
    

class Contact(SyncMarked, Base):
    """One person. Belongs to exactly one client, lead or (standalone) project.

    ``is_primary`` marks the main person of that record; at most one per record.
    """
    __tablename__ = 'contacts'
    __table_args__ = tuple(
        Index(f'uq_contacts_primary_{key}', key, unique=True,
              postgresql_where=text('is_primary'), sqlite_where=text('is_primary'))
        for key in ('client_id', 'lead_id', 'project_id'))

    id = Column(Integer, primary_key=True)
    uuid = Column(Uuid, nullable=False, unique=True, default=uuid4)  # stable id for offline sync
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False, index=True)

    client_id = Column(Integer, ForeignKey('clients.id'), nullable=True)
    lead_id = Column(Integer, ForeignKey('leads.id'), nullable=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)
    is_primary = Column(Boolean, nullable=False, default=False, server_default=false())

    first_name = Column(String(100))
    last_name = Column(String(100))
    title = Column(String(100))
    email = Column(String(120), index=True)
    phone = Column(String(20))
    phone_label = Column(String(20), default="work")
    secondary_phone = Column(String(20), nullable=True)
    secondary_phone_label = Column(String(20), nullable=True)
    notes = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    # Tombstone: hidden from every read, kept for offline devices (app/utils/tombstones.py).
    deleted_at = Column(DateTime, nullable=True)
    deleted_by = Column(Integer, ForeignKey('users.id'), nullable=True)

    client = relationship("Client", backref=backref("contacts", lazy="selectin"))
    lead = relationship("Lead", backref=backref("contacts", lazy="selectin"))
    project = relationship("Project", foreign_keys=[project_id], backref=backref("contacts", lazy="selectin"))

    @hybrid_property
    def display_name(self):
        return full_name(self.first_name, self.last_name)

    @display_name.expression
    def display_name(cls):
        return func.nullif(func.trim(func.coalesce(cls.first_name, '') + ' ' + func.coalesce(cls.last_name, '')), '')

    @property
    def has_content(self):
        return any(getattr(self, column) for column in CONTACT_CONTENT)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.display_name or '',
            "title": self.title,
            "email": self.email,
            "phone": self.phone,
            "phone_label": self.phone_label,
        }

    def __repr__(self):
        return f"<Contact {self.display_name}>"


def _project_field(column, field):
    def fget(self):
        changes = self.__dict__.get('_contact_changes')
        if changes and field in changes:
            return changes[field]
        return _contact_value(self.primary_contact, column)

    def fset(self, value):
        # Resolved at flush (resolve_contacts): linking may need to find the
        # person among the parent's existing contacts.
        self.__dict__.setdefault('_contact_changes', {})[field] = value
        # Not a column change, so tell the session the project needs a flush.
        flag_dirty(self)

    def expr(cls):
        value = Contact.display_name if column is None else getattr(Contact, column)
        return (select(value).where(Contact.id == cls.primary_contact_id, Contact.deleted_at.is_(None))
                .correlate_except(Contact).scalar_subquery())

    return hybrid_property(fget, fset, expr=expr)


class ProjectContactFields:
    """A project points at one contact instead of keeping its own copy.

    A project under a client or lead points at one of that record's contacts
    (matched by email or name, otherwise added there). A standalone project owns
    its contact. Clearing every field unlinks it and drops a project-owned one.
    """


for _field, _column in PROJECT_CONTACT_COLUMNS.items():
    setattr(ProjectContactFields, _field, _project_field(_column, _field))


class Project(ProjectContactFields, SyncMarked, Base):
    __tablename__ = 'projects'

    id = Column(Integer, primary_key=True)
    uuid = Column(Uuid, nullable=False, unique=True, default=uuid4)  # stable id for offline sync
    lead_id = Column(Integer, ForeignKey('leads.id'), nullable=True)
    client_id = Column(Integer, ForeignKey('clients.id'), nullable=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False, index=True)
    project_name = Column(String(255), nullable=False)
    archived_at = Column(DateTime, nullable=True, index=True)
    archived_by = Column(Integer, nullable=True)
    archive_history = Column(JSON, nullable=True)
    project_description = Column(Text, nullable=True)
    type = Column(String(50), nullable=True, default="None")
    # primary_contact_name/_title/_email/_phone/_phone_label are the linked
    # contact's fields; see ProjectContactFields. The old columns remain, frozen.
    primary_contact_id = Column(Integer, ForeignKey('contacts.id', ondelete='SET NULL', use_alter=True,
                                                    name='projects_primary_contact_id_fkey'), nullable=True)
    notes = Column(Text, nullable=True)
    project_status = Column(String(20), nullable=False) # Valid values: "pending", "won", "lost"
    project_start = Column(DateTime, nullable=True)
    project_end = Column(DateTime, nullable=True)
    project_worth = Column(Float, nullable=True)
    value_type = Column(String(20), nullable=True, default='one_time')  # 'one_time', 'monthly', 'yearly'
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_updated_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    deleted_at = Column(DateTime, nullable=True)
    deleted_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    assigned_to = Column(Integer, ForeignKey('users.id'), nullable=True)

    # ✅ Relationships to access names in API
    client = relationship("Client", backref="projects")
    lead = relationship("Lead", backref="projects")
    primary_contact = relationship("Contact", foreign_keys=[primary_contact_id], post_update=True, lazy="selectin")
    assigned_user = relationship("User", foreign_keys=[assigned_to])
    created_by_user = relationship("User", foreign_keys=[created_by])

    def __repr__(self):
        return f"<Project {self.project_name}>"
    

class Interaction(SyncMarked, Base):
    __tablename__ = 'interactions'

    id = Column(Integer, primary_key=True)
    uuid = Column(Uuid, nullable=False, unique=True, default=uuid4)  # stable id for offline sync
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    lead_id = Column(Integer, ForeignKey('leads.id'), nullable=True)
    client_id = Column(Integer, ForeignKey('clients.id'), nullable=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)  # 🆕 NEW FIELD
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False, index=True)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=True)  # NULL only for legacy rows
    contact_person = Column(String)
    email = Column(String)
    phone = Column(String)
    contact_date = Column(DateTime, default=datetime.utcnow)
    outcome = Column(String(255))
    notes = Column(Text)
    follow_up = Column(DateTime, nullable=True)
    followup_status = Column(Enum(FollowUpStatus), default=FollowUpStatus.pending, nullable=False)
    summary = Column(String(255))
    # Tombstone: hidden from every read, kept for offline devices (app/utils/tombstones.py).
    deleted_at = Column(DateTime, nullable=True)
    deleted_by = Column(Integer, ForeignKey('users.id'), nullable=True)

    # Relationships
    lead = relationship("Lead", backref="interactions")
    client = relationship("Client", backref="interactions")
    project = relationship("Project", backref="interactions")  # 🆕 NEW RELATIONSHIP

    def __repr__(self):
        return f"<Interaction {self.id} on {self.contact_date}>"
    

class ActivityType(enum.Enum):
    viewed = "viewed"
    created = "created"
    edited = "edited"
    deleted = "deleted"

class ActivityLog(Base):
    __tablename__ = 'activity_logs'

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    action = Column(Enum(ActivityType), nullable=False)
    entity_type = Column(String(50), nullable=False)  # "client", "lead"
    entity_id = Column(Integer, nullable=False)
    timestamp = Column(DateTime, default=datetime.now, index=True)
    description = Column(Text)

    def __repr__(self):
        return f"<ActivityLog {self.action.value} {self.entity_type} {self.entity_id}>"


class ChatMessage(Base):
    __tablename__ = 'chat_messages'

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False, index=True)
    sender_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    recipient_id = Column(Integer, ForeignKey('users.id'), nullable=True)  # null for room chats
    room = Column(String(100), nullable=True)  # null for direct messages
    client_id = Column(Integer, ForeignKey('clients.id'), nullable=True)
    lead_id = Column(Integer, ForeignKey('leads.id'), nullable=True)
    content = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    sender = relationship("User", foreign_keys=[sender_id])
    recipient = relationship("User", foreign_keys=[recipient_id])
    # Let the FK protect linked messages; purging an empty client must not query chat.
    client = relationship("Client", backref=backref("chat_messages", passive_deletes="all"))
    lead = relationship("Lead", backref=backref("chat_messages", passive_deletes="all"))

    def __repr__(self):
        target = self.room or self.recipient_id
        return f"<ChatMessage from {self.sender_id} to {target}>"


class UserPreference(Base):
    __tablename__ = 'user_preferences'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    category = Column(String(50), nullable=False, index=True)
    preference_key = Column(String(100), nullable=False, index=True)
    preference_value = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", backref="preferences")

    # Constraints
    __table_args__ = (
        Index('idx_user_preferences_lookup', 'user_id', 'category', 'preference_key'),
        UniqueConstraint('user_id', 'category', 'preference_key', name='uq_user_category_key'),
    )

    def __repr__(self):
        return f"<UserPreference user_id={self.user_id} {self.category}.{self.preference_key}>"
    

class File(Base):
    __tablename__ = "files"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # Original name the user uploaded (safe to show in UI / as download name)
    filename = Column(String(255), nullable=False)

    # Internal stored filename (e.g., "<uuid>.ext"); helpful when using local disk
    stored_name = Column(String(255), nullable=False)

    # STORAGE-AGNOSTIC POINTER:
    # - Local disk: absolute or normalized relative path (e.g., "./storage/12/<uuid>.pdf")
    # - Backblaze B2: object key within the bucket (e.g., "tenant-12/<uuid>.pdf")
    path = Column(String(1024), nullable=False, index=True)

    size = Column(Integer, nullable=False)          # bytes
    mimetype = Column(String(100), nullable=False)  # e.g., "application/pdf"
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    uploader = relationship("User", foreign_keys=[user_id])

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.filename,
            "size": self.size,
            "uploadedBy": self.uploader.email if self.uploader else None,
            "date": (self.uploaded_at.isoformat() + "Z") if self.uploaded_at else None,
            "mimetype": self.mimetype,
        }

    def __repr__(self):
        return f"<File id={self.id} tenant={self.tenant_id} name={self.filename}>"


class Backup(Base):
    __tablename__ = "backups"

    id = Column(Integer, primary_key=True)

    # Backup metadata
    filename = Column(String(255), nullable=False, index=True)
    backup_type = Column(String(20), default="manual", nullable=False)  # manual|scheduled|pre_restore
    status = Column(String(20), default="pending", nullable=False, index=True)  # pending|in_progress|completed|failed

    # Storage location
    storage_key = Column(String(1024), nullable=True)  # B2 object key
    size_bytes = Column(Integer, nullable=True)
    checksum = Column(String(64), nullable=True)  # SHA-256 checksum

    # Database snapshot metadata
    database_name = Column(String(100), nullable=True)
    database_size_bytes = Column(Integer, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Job tracking
    job_id = Column(String(100), nullable=True, index=True)  # RQ job ID

    # Error tracking
    error_message = Column(Text, nullable=True)

    # Audit trail
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Relationships
    creator = relationship("User", foreign_keys=[created_by])

    def to_dict(self):
        return {
            "id": self.id,
            "filename": self.filename,
            "type": self.backup_type,
            "status": self.status,
            "size": self.size_bytes,
            "database_size": self.database_size_bytes,
            "checksum": self.checksum,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "completed_at": self.completed_at.isoformat() + "Z" if self.completed_at else None,
            "created_by": self.creator.email if self.creator else "system",
            "error": self.error_message,
        }

    def __repr__(self):
        return f"<Backup {self.filename} status={self.status}>"


class Subscription(Base):
    __tablename__ = 'subscriptions'

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False, index=True)
    client_id = Column(Integer, ForeignKey('clients.id'), nullable=False, index=True)

    plan_name = Column(String(255), nullable=False)
    price = Column(Float, nullable=False)
    billing_cycle = Column(String(20), nullable=False)  # 'monthly' or 'yearly'
    start_date = Column(DateTime, nullable=False)
    renewal_date = Column(DateTime, nullable=True)  # next renewal date
    status = Column(String(20), nullable=False, default='active')  # 'active', 'cancelled', 'paused'

    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)
    updated_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    cancelled_at = Column(DateTime, nullable=True)

    client = relationship("Client", backref="subscriptions")
    created_by_user = relationship("User", foreign_keys=[created_by])

    def __repr__(self):
        return f"<Subscription {self.plan_name} client_id={self.client_id}>"


class BackupRestore(Base):
    __tablename__ = "backup_restores"

    id = Column(Integer, primary_key=True)
    backup_id = Column(Integer, ForeignKey("backups.id"), nullable=False, index=True)
    restored_by = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Pre-restore safety snapshot (automatic backup before restore)
    pre_restore_backup_id = Column(Integer, ForeignKey("backups.id"), nullable=True)

    status = Column(String(20), default="in_progress", nullable=False)  # in_progress|completed|failed
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    # Relationships
    backup = relationship("Backup", foreign_keys=[backup_id])
    pre_restore_backup = relationship("Backup", foreign_keys=[pre_restore_backup_id])
    restorer = relationship("User", foreign_keys=[restored_by])

    def to_dict(self):
        return {
            "id": self.id,
            "backup_id": self.backup_id,
            "pre_restore_backup_id": self.pre_restore_backup_id,
            "restored_by": self.restorer.email if self.restorer else None,
            "status": self.status,
            "started_at": self.started_at.isoformat() + "Z" if self.started_at else None,
            "completed_at": self.completed_at.isoformat() + "Z" if self.completed_at else None,
            "error": self.error_message,
        }

    def __repr__(self):
        return f"<BackupRestore backup_id={self.backup_id} status={self.status}>"


# Match the mobile_stable_ids and mobile_change_tracking migrations on schemas
# built from these models (create_all in tests), so raw inserts that omit these
# columns behave as in the real database. PostgreSQL only: SQLite has no
# gen_random_uuid(), and its ORM inserts use the Python defaults above.
for _synced in (Client, Lead, Project, Contact, Interaction, Account):
    event.listen(_synced.__table__, 'after_create', DDL(
        "ALTER TABLE %(fullname)s ALTER COLUMN uuid SET DEFAULT gen_random_uuid(), "
        "ALTER COLUMN created_at SET DEFAULT timezone('utc', now()), "
        "ALTER COLUMN updated_at SET DEFAULT timezone('utc', now())").execute_if(dialect='postgresql'))


class SyncPurge(Base):
    """One permanently deleted sync record, written by a trigger; kept 90 days."""
    __tablename__ = 'sync_purges'
    id = Column(BigInteger, primary_key=True)
    tenant_id = Column(Integer, nullable=False)  # no FK: tenant teardown must still work
    entity = Column(String(20), nullable=False)
    record_uuid = Column(Uuid, nullable=False)
    purged_xid = Column(BigInteger, nullable=False)
    purged_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (Index('ix_sync_purges_tenant_xid', 'tenant_id', 'purged_xid'),)



class WriteReceipt(Base):
    """First successful outcome of an Idempotency-Key write; kept 90 days (app/services/idempotency.py)."""
    __tablename__ = 'write_receipts'
    # SQLite autoincrements only an INTEGER primary key (development schemas).
    id = Column(BigInteger().with_variant(Integer, 'sqlite'), primary_key=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False)
    user_id = Column(Integer, nullable=False)
    request_key = Column(String(64), nullable=False)
    endpoint = Column(String(100), nullable=False)
    input_hash = Column(String(64), nullable=False)
    status_code = Column(Integer, nullable=False)
    response = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint('tenant_id', 'user_id', 'request_key', name='uq_write_receipts_key'),
                      Index('ix_write_receipts_created', 'created_at'))



class Device(Base):
    """A registered mobile device and its rotating refresh token (app/services/devices.py)."""
    __tablename__ = 'devices'
    __table_args__ = (
        ForeignKeyConstraint(['tenant_id', 'user_id'], ['users.tenant_id', 'users.id'],
                             name='fk_devices_user_same_tenant'),
        UniqueConstraint('tenant_id', 'id', name='uq_devices_tenant_id_id'),
        Index('ix_devices_tenant_user', 'tenant_id', 'user_id'),
    )
    id = Column(Uuid, primary_key=True, default=uuid4)
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False)
    user_id = Column(Integer, nullable=False)
    name = Column(String(100), nullable=False)
    platform = Column(String(20), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    refresh_hash = Column(String(64), nullable=False)
    previous_refresh_hash = Column(String(64), nullable=True)
    previous_valid_until = Column(DateTime, nullable=True)  # fixed grace deadline for the previous secret
    refreshed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    refresh_expires_at = Column(DateTime, nullable=False)
    credential_marker = Column(String(64), nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    revoked_by = Column(Integer, nullable=True)
    # Change-feed acknowledgement state (app/services/sync_feed.py, device mode).
    sync_generation = Column(Integer, nullable=False, default=0, server_default=text('0'))
    sync_acked_seq = Column(BigInteger, nullable=False, default=0, server_default=text('0'))
    sync_pending_seq = Column(BigInteger, nullable=True)
    sync_pending_removals = Column(JSON, nullable=True)


class DeviceHolding(Base):
    """A record the change feed has sent to a device and not yet seen it acknowledge removing."""
    __tablename__ = 'device_holdings'
    __table_args__ = (
        ForeignKeyConstraint(['tenant_id', 'device_id'], ['devices.tenant_id', 'devices.id'],
                             name='fk_device_holdings_device'),
    )
    device_id = Column(Uuid, primary_key=True)
    entity = Column(String(20), primary_key=True)
    record_uuid = Column(Uuid, primary_key=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False)


# Change-feed indexes and triggers (mobile_change_feed migration).
class MobileRecordRef(Base):
    """The phone's own reference for a record it created (app/services/mobile_records.py).

    Per tenant, so a ref never says anything about another tenant. Written in the
    same transaction as the create and never changed; kept at least 90 days.
    """
    __tablename__ = 'mobile_record_refs'
    id = Column(BigInteger().with_variant(Integer, 'sqlite'), primary_key=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False)
    ref = Column(Uuid, nullable=False)
    entity = Column(String(20), nullable=False)
    record_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint('tenant_id', 'ref', name='uq_mobile_record_refs_ref'),
                      Index('ix_mobile_record_refs_created', 'created_at'))


from app.utils.sync_markers import install_sync_ddl  # noqa: E402
for _synced in (Client, Lead, Project, Contact, Interaction, Account):
    _name = _synced.__tablename__
    Index(f'ix_{_name}_tenant_sync_change', _synced.tenant_id, _synced.sync_change_xid)
    Index(f'ix_{_name}_tenant_sync_access', _synced.tenant_id, _synced.sync_access_xid)
install_sync_ddl(Base.metadata)

def storage_column(model, key):
    """The column that stores ``key`` for ``model``, following person fields to contacts.

    Length checks and tool schemas use this, since contact_person and the other
    person fields are stored on Contact rather than on the record itself.
    """
    column = model.__table__.c.get(key)
    if column is not None:
        return column
    mapping = CONTACT_COLUMNS if issubclass(model, PrimaryContactFields) else (
        PROJECT_CONTACT_COLUMNS if issubclass(model, ProjectContactFields) else {})
    if key not in mapping:
        return None
    return Contact.__table__.c[mapping[key] or 'first_name']


def record_fields(record):
    """Column values plus the person fields, as the API presents the record."""
    names = [column.name for column in record.__table__.columns]
    if isinstance(record, PrimaryContactFields):
        names += list(CONTACT_COLUMNS)
    elif isinstance(record, ProjectContactFields):
        names += list(PROJECT_CONTACT_COLUMNS)
    return {name: getattr(record, name) for name in names}

# --- Contact resolution at flush ------------------------------------------------
from sqlalchemy import event as _event, or_ as _or
from sqlalchemy.orm import Session as _Session


def _link_project_contact(session, project):
    changes = project.__dict__.pop('_contact_changes')
    contact = project.primary_contact
    values = {field: _contact_value(contact, column) for field, column in PROJECT_CONTACT_COLUMNS.items()}
    values.update(changes)
    content = [values[f] for f in ('primary_contact_name', 'primary_contact_title',
                                   'primary_contact_email', 'primary_contact_phone')]
    if not any(content):
        if contact is not None:
            project.primary_contact = None
            if contact.project_id is not None and contact.project_id == project.id:
                session.delete(contact)
        return
    if contact is not None:
        for field, value in changes.items():
            _set_contact_value(contact, PROJECT_CONTACT_COLUMNS[field], value)
        return
    if project.client_id is not None:
        owner = {'client_id': project.client_id}
    elif project.lead_id is not None:
        owner = {'lead_id': project.lead_id}
    else:
        owner = None
    if owner is not None:
        key, owner_id = next(iter(owner.items()))
        email = (values['primary_contact_email'] or '').strip().lower()
        name = ' '.join((values['primary_contact_name'] or '').split()).lower()
        with session.no_autoflush:
            candidates = session.query(Contact).filter(getattr(Contact, key) == owner_id).order_by(Contact.id).all()
        match = next((c for c in candidates if email and (c.email or '').strip().lower() == email), None) or \
            next((c for c in candidates if name and (c.display_name or '').lower() == name), None)
        if match is not None:
            # Fill gaps only: the person's existing details stay authoritative.
            for field, column in PROJECT_CONTACT_COLUMNS.items():
                if values[field] and not _contact_value(match, column):
                    _set_contact_value(match, column, values[field])
            project.primary_contact = match
            return
        contact = Contact(tenant_id=project.tenant_id, is_primary=False, **owner)
    else:
        contact = Contact(tenant_id=project.tenant_id, is_primary=True)
        contact.project = project
    for field, column in PROJECT_CONTACT_COLUMNS.items():
        if values[field] is not None:
            _set_contact_value(contact, column, values[field])
    session.add(contact)
    project.primary_contact = contact


@_event.listens_for(_Session, "before_flush")
def resolve_contacts(session, flush_context, instances):
    for obj in list(session.new) + list(session.dirty):
        if isinstance(obj, Project) and '_contact_changes' in obj.__dict__:
            _link_project_contact(session, obj)
    for obj in list(session.new) + list(session.dirty):
        if not isinstance(obj, Contact) or obj in session.deleted:
            continue
        if obj.tenant_id is None:
            parent = obj.client or obj.lead or obj.project
            obj.tenant_id = parent.tenant_id if parent is not None else None
        # A primary person whose details were all cleared is no longer a person.
        if obj.is_primary and not obj.has_content:
            for parent in (obj.client, obj.lead):
                if parent is not None and obj in parent.contacts:
                    parent.contacts.remove(obj)
            if obj in session.new:
                session.expunge(obj)
            else:
                session.delete(obj)
