from sqlalchemy import Column, Integer, String, DateTime, Text, Float, ForeignKey, Table, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base
from sqlalchemy import Enum, Index, UniqueConstraint, JSON
import enum

# Association table for many-to-many User ↔ Role
user_roles = Table(
    'user_roles',
    Base.metadata,
    Column('user_id', Integer, ForeignKey('users.id')),
    Column('role_id', Integer, ForeignKey('roles.id')),
)

class FollowUpStatus(enum.Enum):
    pending = "pending"
    contacted = "contacted"
    completed = "completed"
    rescheduled = "rescheduled"


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, nullable=False, index=True)
    email = Column(String(120), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    roles = relationship("Role", secondary=user_roles, back_populates="users")

    def __repr__(self):
        return f"<User {self.email}>"


class Role(Base):
    __tablename__ = 'roles'
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)

    users = relationship("User", secondary=user_roles, back_populates="roles")

    def __repr__(self):
        return f"<Role {self.name}>"


class Client(Base):
    __tablename__ = 'clients'
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, nullable=False)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    updated_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)
    deleted_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    assigned_to = Column(Integer, ForeignKey('users.id'), nullable=True)
    name = Column(String(100), nullable=False)
    contact_person = Column(String(100))
    contact_title = Column(String(100))
    email = Column(String(120), index=True)
    phone = Column(String(20))
    phone_label = Column(String(20), default="work")
    secondary_phone = Column(String(20), nullable=True)
    secondary_phone_label = Column(String(20), nullable=True)
    address = Column(String(255))
    city = Column(String(100))
    state = Column(String(100))
    zip = Column(String(20))
    status = Column(String(50), default='new')
    type = Column(String(50), nullable=True, default="None")
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)

    # Relationships
    assigned_user = relationship("User", foreign_keys=[assigned_to])
    created_by_user = relationship("User", foreign_keys=[created_by])

    def __repr__(self):
        return f"<Client {self.name}>"


class Account(Base):
    __tablename__ = 'accounts'

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey('clients.id'), nullable=False)
    tenant_id = Column(Integer, nullable=False)
    account_number = Column(String(100), nullable=False, unique=True)
    account_name = Column(String(255), nullable=True)
    status = Column(String(50), default="active")
    opened_on = Column(DateTime, default=datetime.utcnow)
    notes = Column(Text)

    client = relationship("Client", backref="accounts")

    def __repr__(self):
        return f"<Account {self.account_number}>"


class Lead(Base):
    __tablename__ = 'leads'
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, nullable=False)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    updated_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)
    deleted_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    assigned_to = Column(Integer, ForeignKey('users.id'), nullable=True)
    name = Column(String(100), nullable=False)
    contact_person = Column(String(100))
    contact_title = Column(String(100))
    email = Column(String(120), index=True)
    phone = Column(String(20))
    phone_label = Column(String(20), default="work")
    secondary_phone = Column(String(20), nullable=True)
    secondary_phone_label = Column(String(20), nullable=True)
    address = Column(String(255))
    city = Column(String(100))
    state = Column(String(100))
    zip = Column(String(20))
    type = Column(String(50), nullable=True, default="None")
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    lead_status = Column(String(20), default="open")  # Valid: "open", "converted", "closed", "lost"
    converted_on = Column(DateTime, nullable=True)

    assigned_user = relationship("User", foreign_keys=[assigned_to])
    created_by_user = relationship("User", foreign_keys=[created_by])


    def __repr__(self):
        return f"<Lead {self.name}>"
    

class Contact(Base):
    __tablename__ = 'contacts'

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, nullable=False, index=True)

    client_id = Column(Integer, ForeignKey('clients.id'), nullable=True)
    lead_id = Column(Integer, ForeignKey('leads.id'), nullable=True)

    first_name = Column(String(100))
    last_name = Column(String(100))
    title = Column(String(100))
    email = Column(String(120), index=True)
    phone = Column(String(20))
    phone_label = Column(String(20), default="work")
    secondary_phone = Column(String(20), nullable=True)
    secondary_phone_label = Column(String(20), nullable=True)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", backref="contacts")
    lead = relationship("Lead", backref="contacts")

    def to_dict(self):
        return {
            "id": self.id,
            "name": f"{self.first_name} {self.last_name}".strip(),
            "title": self.title,
            "email": self.email,
            "phone": self.phone,
            "phone_label": self.phone_label,
        }

    def __repr__(self):
        return f"<Contact {self.first_name} {self.last_name}>"


class Project(Base):
    __tablename__ = 'projects'

    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey('leads.id'), nullable=True)
    client_id = Column(Integer, ForeignKey('clients.id'), nullable=True)
    tenant_id = Column(Integer, nullable=False)
    project_name = Column(String(255), nullable=False)
    project_description = Column(Text, nullable=True)
    type = Column(String(50), nullable=True, default="None")
    primary_contact_name = Column(String(100), nullable=True)
    primary_contact_title = Column(String(100), nullable=True)
    primary_contact_email = Column(String(120), nullable=True)
    primary_contact_phone = Column(String(20), nullable=True)
    primary_contact_phone_label = Column(String(20), nullable=True)
    notes = Column(Text, nullable=True)
    project_status = Column(String(20), nullable=False) # Valid values: "pending", "won", "lost"
    project_start = Column(DateTime, nullable=True)
    project_end = Column(DateTime, nullable=True)
    project_worth = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)
    last_updated_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    deleted_at = Column(DateTime, nullable=True)
    deleted_by = Column(Integer, ForeignKey('users.id'), nullable=True)

    # ✅ Relationships to access names in API
    client = relationship("Client", backref="projects")
    lead = relationship("Lead", backref="projects")

    def __repr__(self):
        return f"<Project {self.project_name}>"
    

class Interaction(Base):
    __tablename__ = 'interactions'

    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey('leads.id'), nullable=True)
    client_id = Column(Integer, ForeignKey('clients.id'), nullable=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)  # 🆕 NEW FIELD
    tenant_id = Column(Integer, nullable=False)
    contact_person = Column(String)
    email = Column(String)
    phone = Column(String)
    contact_date = Column(DateTime, default=datetime.utcnow)
    outcome = Column(String(255))
    notes = Column(Text)
    follow_up = Column(DateTime, nullable=True)
    followup_status = Column(Enum(FollowUpStatus), default=FollowUpStatus.pending, nullable=False)
    summary = Column(String(255))

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
    tenant_id = Column(Integer, nullable=False, index=True)
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
    tenant_id = Column(Integer, nullable=False, index=True)
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
    client = relationship("Client", backref="chat_messages")
    lead = relationship("Lead", backref="chat_messages")

    def __repr__(self):
        target = self.room or self.recipient_id
        return f"<ChatMessage from {self.sender_id} to {target}>"


class Message(Base):
    __tablename__ = 'messages'

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, nullable=False, index=True)
    sender_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    receiver_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    body = Column(Text, nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow)
    read = Column(Boolean, default=False)

    sender = relationship("User", foreign_keys=[sender_id])
    receiver = relationship("User", foreign_keys=[receiver_id])


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
    tenant_id = Column(Integer, nullable=False, index=True)
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
