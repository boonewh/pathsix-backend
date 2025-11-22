# PathSix CRM Backend

A Quart-based (async Flask) backend for the PathSix CRM system.

## Quick Setup

### 1. Prerequisites
- Python 3.12+ 
- Virtual environment

### 2. Installation & Setup

```bash
# The virtual environment should already be created and activated
# All dependencies are already installed

# Set up the database (first time only)
python -c "from app.database import Base, engine; Base.metadata.create_all(bind=engine)"
python seed_roles.py

# Create an admin user
python create_tenant_admin.py
```

### 3. Running the Application

```bash
python run.py
```

The server will start on `http://localhost:8000`

## Environment Configuration

The application uses the following default configuration for local development:

- **Database**: SQLite (`app.db`)
- **Port**: 8000
- **Storage**: Local filesystem (`./storage`)
- **Frontend URL**: http://localhost:5173

## Project Structure

```
app/
├── __init__.py          # App factory and configuration
├── config.py           # Configuration settings
├── database.py         # Database connection and session
├── models.py           # SQLAlchemy models
├── routes/             # API endpoints
│   ├── auth.py         # Authentication
│   ├── clients.py      # Client management
│   ├── projects.py     # Project management
│   ├── users.py        # User management
│   └── ...
└── utils/              # Utility functions
    ├── auth_utils.py   # Authentication helpers
    ├── email_utils.py  # Email utilities
    └── ...
```

## API Features

- **Authentication**: JWT-based authentication
- **Multi-tenant**: Support for multiple tenants
- **CRUD Operations**: For clients, projects, leads, contacts
- **File Storage**: Local and S3-compatible storage
- **Email**: SMTP email sending
- **Search**: Full-text search capabilities
- **Reports**: Business reporting features

## Development Notes

- Uses Quart (async Flask) for better performance
- SQLAlchemy ORM with SQLite for local development
- CORS configured for frontend integration
- Background tasks for database keep-alive
- Structured logging and error handling

## Production Deployment

For production, consider:
- PostgreSQL instead of SQLite
- Hypercorn or Uvicorn as ASGI server
- Environment variables for sensitive configuration
- S3-compatible storage for file uploads
- Proper SMTP configuration for emails