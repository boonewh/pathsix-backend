@echo off
REM Windows batch script for PathSix CRM Backend setup

echo 🚀 Setting up PathSix CRM Backend...

REM Load environment variables from .env file
if exist .env (
    echo 📋 Loading environment variables from .env...
    for /f "tokens=*" %%i in ('type .env ^| findstr /v "^#" ^| findstr "="') do set %%i
) else (
    echo ⚠️ No .env file found, using defaults
)

REM Activate virtual environment
call venv\Scripts\activate.bat
echo ✅ Virtual environment activated

REM Check if database exists, if not create tables
if not exist app.db (
    echo 📦 Creating database tables...
    python -c "from app.database import Base, engine; Base.metadata.create_all(bind=engine); print('Database tables created successfully')"
    
    echo 👤 Seeding roles...
    python seed_roles.py
    
    echo ✅ Database setup complete
) else (
    echo ✅ Database already exists
)

echo.
echo 🎯 Setup complete! You can now:
echo    1. Create a tenant: python create_default_tenant.py (or create_custom_tenant.py)
echo    2. Start the server: python run.py
echo.
echo 🌐 Server will run on: http://localhost:8000
echo 📁 Frontend should point to: http://localhost:8000

pause