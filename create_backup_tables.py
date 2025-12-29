#!/usr/bin/env python
"""
Quick script to create backup tables in production.
Run this via: flyctl ssh console --app pathsixsolutions-backend --command "python create_backup_tables.py"
"""
import os
os.environ.setdefault('DATABASE_URL', os.getenv('DATABASE_URL', '').replace('postgres://', 'postgresql://'))

from app.database import engine
from app.models import Base, Backup, BackupRestore

print("Creating backup tables...")
print(f"Database URL: {os.getenv('DATABASE_URL')[:50]}...")

# Create only the backup tables
Backup.__table__.create(engine, checkfirst=True)
BackupRestore.__table__.create(engine, checkfirst=True)

print("✅ Backup tables created successfully!")