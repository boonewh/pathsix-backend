# PathSix CRM Backend

A Quart-based (async Flask) backend for the PathSix CRM system.

---

## Quick Setup

### 1. Prerequisites
- Python 3.12+
- Virtual environment (already set up)
- PostgreSQL (production) or SQLite (local dev)

### 2. Initial Setup

```bash
# Create database tables
python -c "from app.database import Base, engine; Base.metadata.create_all(bind=engine)"

# Seed required roles
python seed_roles.py

# Load demo data (optional)
python seed_test_data.py
```

**Default Demo Credentials:**
- Admin: `admin@demo.com` / `admin123`
- User: `user1@demo.com` / `user123`

### 3. Running the Application

```bash
# Local development
python run.py
```

Server runs on `http://localhost:8000`

### 4. Running Database Migrations

```bash
# Run pending migrations
alembic upgrade head

# Create new migration
alembic revision --autogenerate -m "description"
```

---

## Environment Configuration

Local development defaults (`.env` file):

```env
# Database
DATABASE_URL=sqlite:///app.db

# Application
PORT=8000
FRONTEND_URL=http://localhost:5173
SECRET_KEY=your-secret-key-here

# Storage (B2/S3)
STORAGE_VENDOR=s3
S3_ENDPOINT_URL=s3.us-east-005.backblazeb2.com
S3_BUCKET=pathsix-vault
S3_ACCESS_KEY_ID=your_key
S3_SECRET_ACCESS_KEY=your_secret

# Backups (separate B2 bucket)
BACKUP_S3_ENDPOINT_URL=https://s3.us-east-005.backblazeb2.com
BACKUP_S3_BUCKET=pathsixdesigns-vault
BACKUP_S3_ACCESS_KEY_ID=your_key
BACKUP_S3_SECRET_ACCESS_KEY=your_secret
BACKUP_GPG_PASSPHRASE=your-secure-passphrase
BACKUP_RETENTION_DAYS=30

# Email (SMTP)
MAIL_SERVER=mail.gandi.net
MAIL_PORT=587
MAIL_USERNAME=support@pathsixdesigns.com
MAIL_PASSWORD=your_password
MAIL_USE_TLS=true

# Monitoring
SENTRY_DSN=your_sentry_dsn_here
```

---

## Project Structure

```
app/
├── __init__.py              # App factory and configuration
├── config.py                # Environment configuration
├── database.py              # Database session management
├── models.py                # SQLAlchemy models
├── routes/                  # API endpoints
│   ├── auth.py              # Authentication (login, signup, password reset)
│   ├── clients.py           # Client CRUD + bulk operations
│   ├── leads.py             # Lead CRUD + bulk operations
│   ├── projects.py          # Project CRUD + bulk operations
│   ├── interactions.py      # Interaction tracking
│   ├── contacts.py          # Contact management
│   ├── users.py             # User management (admin)
│   ├── reports.py           # 10 business reports
│   ├── admin_backups.py     # Database backup/restore (admin)
│   ├── search.py            # Global search
│   └── storage.py           # File upload/download
├── utils/                   # Utility functions
│   ├── auth_utils.py        # JWT and password hashing
│   ├── email_utils.py       # SMTP email sending
│   └── backup_storage.py    # B2 backup storage backend
└── workers/                 # Background jobs (future)
    └── backup_jobs.py       # Backup job processing

scripts/
├── run_scheduled_backup.py  # Daily backup script (Fly.io)
└── cleanup_backups.py       # Retention cleanup

migrations/                  # Alembic database migrations
```

---

## API Features

### Core Features
- **Authentication**: JWT-based with email/password
- **Multi-tenant**: Tenant isolation for all data
- **CRUD Operations**: Clients, leads, projects, contacts, interactions
- **Soft Delete**: Trash system for clients, leads, projects
- **File Storage**: S3-compatible (Backblaze B2)
- **Email**: SMTP for signup verification and notifications
- **Search**: Global search across entities
- **Reports**: 10 comprehensive business reports

### Advanced Features
- **Database Backups**: Automated daily backups to B2 with GPG encryption (runs synchronously, no queue needed)
- **Restore System**: Point-in-time restore with automatic safety backups (runs synchronously)
- **Audit Trail**: Permanent restore logs in B2
- **Monitoring**: Sentry error tracking and performance monitoring
- **Long Operation Timeouts**: 10-minute HTTP timeout for backup/restore operations
- **Rate Limiting**: (Future) Redis-based rate limiting

---

## Production Deployment (Fly.io)

### Prerequisites
```bash
# Install Fly CLI
curl -L https://fly.io/install.sh | sh

# Login
flyctl auth login
```

### Initial Deployment

```bash
# Create app
flyctl apps create pathsix-backend

# Set secrets
flyctl secrets set \
  DATABASE_URL="postgresql://..." \
  SECRET_KEY="..." \
  BACKUP_GPG_PASSPHRASE="..." \
  S3_ACCESS_KEY_ID="..." \
  S3_SECRET_ACCESS_KEY="..." \
  SENTRY_DSN="..."

# Deploy
flyctl deploy
```

### Scheduled Backups

```bash
# Create scheduled backup machine
bash setup_scheduled_backup_machine.sh
```

Runs daily backups automatically.

---

## Monitoring & Logging

### View Production Logs

```bash
# Real-time logs
flyctl logs

# Filter for errors
flyctl logs --filter="ERROR"

# Filter for backups
flyctl logs --filter="Backup"

# Last 500 lines
flyctl logs --lines=500
```

### Sentry Error Tracking

- **URL**: https://sentry.io
- **Project**: pathsix-backend
- Automatic error capture with stack traces
- Slow query detection (>200ms)
- Performance monitoring

### Backup Monitoring

```bash
# Check backup status
curl https://your-app.fly.dev/api/admin/backups \
  -H "Authorization: Bearer $TOKEN"

# View scheduled machine
flyctl machine list --app your-app
```

---

## Documentation

Comprehensive guides:

- **[BACKUP_GUIDE.md](BACKUP_GUIDE.md)** - Complete backup and restore system documentation
- **[REPORTS_GUIDE.md](REPORTS_GUIDE.md)** - All 10 business reports with API reference
- **[improvement_map.md](improvement_map.md)** - Architecture, rules, and implementation roadmap

---

## Key Endpoints

### Authentication
- `POST /api/auth/login` - User login
- `POST /api/auth/signup` - New user registration
- `POST /api/auth/forgot-password` - Request password reset
- `POST /api/auth/reset-password` - Reset password with token

### Clients, Leads, Projects
- `GET/POST /api/clients` - List/create
- `GET/PUT/DELETE /api/clients/:id` - Get/update/delete
- `POST /api/clients/bulk-delete` - Soft delete multiple
- `POST /api/clients/:id/restore` - Restore from trash

(Similar patterns for `/api/leads` and `/api/projects`)

### Reports (see [REPORTS_GUIDE.md](REPORTS_GUIDE.md))
- `GET /api/reports/pipeline` - Sales pipeline
- `GET /api/reports/conversion-rate` - Conversion metrics
- `GET /api/reports/revenue-forecast` - Weighted forecast
- ...and 7 more reports

### Backups (Admin Only - see [BACKUP_GUIDE.md](BACKUP_GUIDE.md))
- `GET /api/admin/backups` - List all backups
- `POST /api/admin/backups` - Create manual backup
- `POST /api/admin/backups/:id/restore` - Restore database
- `GET /api/admin/backups/restores` - Restore history

---

## Development Notes

- **Framework**: Quart (async Flask) for better performance
- **ORM**: SQLAlchemy with async support
- **Database**: SQLite (local) / PostgreSQL (production)
- **Auth**: JWT tokens with 30-day expiry
- **CORS**: Configured for multiple frontend origins
- **Logging**: Structured logs with tenant/user context
- **Error Handling**: Sentry integration for production

---

## Testing

```bash
# Run tests (when test suite is added)
pytest

# Check code style
flake8 app/

# Type checking
mypy app/
```

---

## Support

For questions or issues:
1. Check the relevant guide (BACKUP_GUIDE.md, REPORTS_GUIDE.md, etc.)
2. Review logs: `flyctl logs`
3. Check Sentry for errors
4. Consult improvement_map.md for architecture decisions

---

## License

Proprietary - PathSix Solutions
