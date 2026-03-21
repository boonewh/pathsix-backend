"""
Add value_type column to projects table.
Run in production: /venv/bin/python add_value_type_column.py
"""
import os
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

cur.execute("""
    ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS value_type VARCHAR(20) DEFAULT 'one_time';
""")

conn.commit()
cur.close()
conn.close()

print("Done: value_type column added to projects table.")
