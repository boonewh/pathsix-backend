#!/bin/bash
# Setup script for PathSix CRM Backend

echo "🚀 Setting up PathSix CRM Backend..."

# Activate virtual environment
source venv/Scripts/activate

# Set environment variable for SQLite
export DATABASE_URL="sqlite:///app.db"

echo "✅ Virtual environment activated"
echo "✅ Database URL set to SQLite"

# Check if database exists, if not create tables
if [ ! -f "app.db" ]; then
    echo "📦 Creating database tables..."
    python -c "from app.database import Base, engine; Base.metadata.create_all(bind=engine); print('Database tables created successfully')"
    
    echo "👤 Seeding roles..."
    python seed_roles.py
    
    echo "✅ Database setup complete"
else
    echo "✅ Database already exists"
fi

echo ""
echo "🎯 Setup complete! You can now:"
echo "   1. Create an admin user: python create_tenant_admin.py"
echo "   2. Start the server: python run.py"
echo ""
echo "🌐 Server will run on: http://localhost:8000"
echo "📁 Frontend should point to: http://localhost:8000"