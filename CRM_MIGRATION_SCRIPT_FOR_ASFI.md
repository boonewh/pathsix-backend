# CRM Migration Script for ASFI

## Overview

This document contains instructions for migrating ASFI (tenant #1) from the **old CRM database** to the **new multi-tenant CRM system**.

### The Problem

- **Old System**: `pathsix-crm (asfi original)` - Single-tenant, hardcoded config, no `tenants` table
- **New System**: `pathsix-backend` + `pathsix-crm-custom` - Multi-tenant, dynamic config from database

ASFI is currently using the old system with real production data. When the new CRM is ready, we need to upgrade their database schema to work with the new frontend/backend.

### Migration Strategy: Upgrade In-Place

We will **add new schema elements to the existing ASFI database** rather than copying data to a new database. This is safer because:
- No risk of data loss during copy
- Preserves all IDs and relationships
- Minimal downtime
- 95% of the schema is already identical

---

## What Needs to Change

### New Table: `tenants`

```sql
CREATE TABLE tenants (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    slug VARCHAR(50) UNIQUE NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    config JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_tenants_slug ON tenants(slug);
```

### New Column: `leads.lead_source`

```sql
ALTER TABLE leads ADD COLUMN lead_source VARCHAR(50);
CREATE INDEX idx_leads_lead_source ON leads(lead_source);
```

### Modified Constraint: `users.tenant_id` Foreign Key

The old schema has `tenant_id` as a plain integer. The new schema has it as a foreign key to `tenants.id`. We add this constraint AFTER creating the tenants table and inserting ASFI's tenant record.

```sql
ALTER TABLE users
ADD CONSTRAINT fk_users_tenant
FOREIGN KEY (tenant_id) REFERENCES tenants(id);
```

### Optional Tables (Feature Disabled)

The `backups` and `backup_restores` tables exist in the new schema but the feature is disabled. You can skip these or add them for future use.

---

## Pre-Migration Checklist

- [ ] **Backup the production database** before doing anything
- [ ] **Test the migration** on a database backup/copy first
- [ ] **Schedule downtime** - users should not be using the CRM during migration
- [ ] **Verify existing tenant_id values** - all ASFI users should have `tenant_id = 1`
- [ ] **Prepare ASFI's tenant config** - customize the JSON config below for ASFI's branding/labels

---

## ASFI Tenant Configuration

**IMPORTANT**: The config below must include ALL required fields or the frontend will crash.

Edit this config to match ASFI's actual branding and preferences:

```python
ASFI_CONFIG = {
    "branding": {
        "companyName": "All Seasons Foam Insulation",
        "primaryColor": "#1E40AF",      # ASFI's brand color (adjust as needed)
        "secondaryColor": "#F59E0B",
        "logo": None,                   # Or "/logos/asfi-logo.png" if they have one
        "logoCompact": None,
    },
    "labels": {
        "client": "Account",            # ASFI uses "Account" instead of "Client"
        "lead": "Lead",
        "project": "Project",
        "interaction": "Interaction",
    },
    "leads": {
        "statuses": ["new", "contacted", "qualified", "proposal", "won", "lost"],
        "statusConfig": {
            "colors": {
                "new": "bg-gray-100 text-gray-800",
                "contacted": "bg-blue-100 text-blue-800",
                "qualified": "bg-yellow-100 text-yellow-800",
                "proposal": "bg-purple-100 text-purple-800",
                "won": "bg-green-100 text-green-800",
                "lost": "bg-red-100 text-red-800"
            },
            "labels": {
                "new": "New",
                "contacted": "Contacted",
                "qualified": "Qualified",
                "proposal": "Proposal",
                "won": "Won",
                "lost": "Lost"
            }
        },
        "sources": [
            "Website", "Referral", "Cold Call", "Email Campaign",
            "Social Media", "Trade Show", "Advertisement", "Partner", "Other"
        ],
        "temperatures": ["hot", "warm", "cold"],
        "temperatureConfig": {
            "colors": {
                "hot": "text-red-600",
                "warm": "text-orange-600",
                "cold": "text-blue-600"
            }
        }
    },
    "businessTypes": [
        "None", "Residential", "Commercial", "Industrial",
        "Agricultural", "New Construction", "Retrofit", "Other"
    ],
    "regional": {
        "currency": "USD",
        "currencySymbol": "$",
        "dateFormat": "MM/DD/YYYY",
        "phoneFormat": "US",
        "addressFormat": "US"
    },
    "features": {
        "showTemperature": True,
        "showLeadScore": False,
        "showSource": True,
        "enableProjectStandalone": True,
        "enableBulkOperations": True,
        "enableAdvancedFilters": True,
        "enableDataExport": True
    },
    "defaults": {
        "leadsPerPage": 10,
        "clientsPerPage": 10,
        "projectsPerPage": 10,
        "defaultView": "cards",
        "defaultSort": "newest"
    }
}
```

**Critical Fields** (frontend will crash if missing):
- `businessTypes` - used by Clients, Leads, Projects pages
- `leads.statuses` - used by Leads page
- `leads.sources` - used by Lead form

---

## Migration Script

Save this as `migrate_asfi_database.py` and run it against the ASFI production database.

```python
"""
ASFI Database Migration Script
==============================

This script upgrades the ASFI database schema to work with the new multi-tenant CRM.

BEFORE RUNNING:
1. BACKUP THE DATABASE
2. Test on a copy first
3. Schedule downtime
4. Edit ASFI_CONFIG below to match their actual preferences

RUN VIA:
- SSH: flyctl ssh console -a pathsix-db
        /venv/bin/python migrate_asfi_database.py

- Proxy: flyctl proxy 5432 -a pathsix-db
         set DATABASE_URL=postgresql://postgres:PASSWORD@localhost:5432/pathsix_backend
         python migrate_asfi_database.py
"""

import os
import json
import psycopg2
from datetime import datetime

# =============================================================================
# ASFI TENANT CONFIGURATION - EDIT THIS BEFORE RUNNING
# =============================================================================

ASFI_CONFIG = {
    "branding": {
        "companyName": "All Seasons Foam Insulation",
        "primaryColor": "#1E40AF",
        "secondaryColor": "#F59E0B",
        "logo": None,
        "logoCompact": None,
    },
    "labels": {
        "client": "Account",
        "lead": "Lead",
        "project": "Project",
        "interaction": "Interaction",
    },
    "leads": {
        "statuses": ["new", "contacted", "qualified", "proposal", "won", "lost"],
        "statusConfig": {
            "colors": {
                "new": "bg-gray-100 text-gray-800",
                "contacted": "bg-blue-100 text-blue-800",
                "qualified": "bg-yellow-100 text-yellow-800",
                "proposal": "bg-purple-100 text-purple-800",
                "won": "bg-green-100 text-green-800",
                "lost": "bg-red-100 text-red-800"
            },
            "labels": {
                "new": "New",
                "contacted": "Contacted",
                "qualified": "Qualified",
                "proposal": "Proposal",
                "won": "Won",
                "lost": "Lost"
            }
        },
        "sources": [
            "Website", "Referral", "Cold Call", "Email Campaign",
            "Social Media", "Trade Show", "Advertisement", "Partner", "Other"
        ],
        "temperatures": ["hot", "warm", "cold"],
        "temperatureConfig": {
            "colors": {
                "hot": "text-red-600",
                "warm": "text-orange-600",
                "cold": "text-blue-600"
            }
        }
    },
    "businessTypes": [
        "None", "Residential", "Commercial", "Industrial",
        "Agricultural", "New Construction", "Retrofit", "Other"
    ],
    "regional": {
        "currency": "USD",
        "currencySymbol": "$",
        "dateFormat": "MM/DD/YYYY",
        "phoneFormat": "US",
        "addressFormat": "US"
    },
    "features": {
        "showTemperature": True,
        "showLeadScore": False,
        "showSource": True,
        "enableProjectStandalone": True,
        "enableBulkOperations": True,
        "enableAdvancedFilters": True,
        "enableDataExport": True
    },
    "defaults": {
        "leadsPerPage": 10,
        "clientsPerPage": 10,
        "projectsPerPage": 10,
        "defaultView": "cards",
        "defaultSort": "newest"
    }
}

# =============================================================================
# MIGRATION SCRIPT - DO NOT EDIT BELOW THIS LINE
# =============================================================================

def get_connection():
    """Get database connection from environment variable."""
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise ValueError("DATABASE_URL environment variable not set")

    # Fix postgres:// vs postgresql:// issue
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)

    return psycopg2.connect(db_url)


def check_prerequisites(cur):
    """Verify the database is ready for migration."""
    print("\n[1/6] Checking prerequisites...")

    # Check if tenants table already exists
    cur.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_name = 'tenants'
        );
    """)
    tenants_exists = cur.fetchone()[0]

    if tenants_exists:
        print("  WARNING: tenants table already exists!")
        cur.execute("SELECT id, name, slug FROM tenants;")
        existing = cur.fetchall()
        for t in existing:
            print(f"    - Tenant {t[0]}: {t[1]} ({t[2]})")

        response = input("  Continue anyway? (y/n): ").strip().lower()
        if response != 'y':
            return False

    # Check existing users' tenant_id values
    cur.execute("SELECT DISTINCT tenant_id FROM users;")
    tenant_ids = [row[0] for row in cur.fetchall()]
    print(f"  Existing user tenant_ids: {tenant_ids}")

    if 1 not in tenant_ids:
        print("  WARNING: No users with tenant_id=1 found!")
        print("  The migration will create tenant id=1 for ASFI.")

    return True


def create_tenants_table(cur):
    """Create the tenants table if it doesn't exist."""
    print("\n[2/6] Creating tenants table...")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tenants (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            slug VARCHAR(50) UNIQUE NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            config JSONB NOT NULL DEFAULT '{}'
        );
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_tenants_slug ON tenants(slug);
    """)

    print("  Done.")


def insert_asfi_tenant(cur):
    """Insert ASFI as tenant #1 with full config."""
    print("\n[3/6] Inserting ASFI tenant record...")

    # Check if tenant already exists
    cur.execute("SELECT id FROM tenants WHERE id = 1 OR slug = 'asfi';")
    existing = cur.fetchone()

    if existing:
        print(f"  Tenant already exists (id={existing[0]}). Updating config...")
        cur.execute("""
            UPDATE tenants
            SET config = %s, name = %s
            WHERE id = 1 OR slug = 'asfi';
        """, (json.dumps(ASFI_CONFIG), "All Seasons Foam Insulation"))
    else:
        # Insert with explicit ID=1 to match existing users
        cur.execute("""
            INSERT INTO tenants (id, name, slug, is_active, config)
            VALUES (1, %s, %s, TRUE, %s);
        """, ("All Seasons Foam Insulation", "asfi", json.dumps(ASFI_CONFIG)))

        # Reset the sequence to avoid ID conflicts
        cur.execute("SELECT setval('tenants_id_seq', (SELECT MAX(id) FROM tenants));")

    print("  Done.")


def add_lead_source_column(cur):
    """Add lead_source column to leads table if it doesn't exist."""
    print("\n[4/6] Adding lead_source column to leads...")

    cur.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.columns
            WHERE table_name = 'leads' AND column_name = 'lead_source'
        );
    """)
    column_exists = cur.fetchone()[0]

    if column_exists:
        print("  Column already exists. Skipping.")
    else:
        cur.execute("""
            ALTER TABLE leads ADD COLUMN lead_source VARCHAR(50);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_leads_lead_source ON leads(lead_source);
        """)
        print("  Done.")


def add_users_foreign_key(cur):
    """Add foreign key constraint from users.tenant_id to tenants.id."""
    print("\n[5/6] Adding foreign key constraint to users.tenant_id...")

    # Check if constraint already exists
    cur.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.table_constraints
            WHERE constraint_name = 'fk_users_tenant'
        );
    """)
    constraint_exists = cur.fetchone()[0]

    if constraint_exists:
        print("  Constraint already exists. Skipping.")
    else:
        # Verify all users have valid tenant_id before adding constraint
        cur.execute("""
            SELECT COUNT(*) FROM users
            WHERE tenant_id NOT IN (SELECT id FROM tenants);
        """)
        orphaned = cur.fetchone()[0]

        if orphaned > 0:
            print(f"  ERROR: {orphaned} users have tenant_id values that don't exist in tenants table!")
            print("  Fix this before adding the foreign key constraint.")
            return False

        cur.execute("""
            ALTER TABLE users
            ADD CONSTRAINT fk_users_tenant
            FOREIGN KEY (tenant_id) REFERENCES tenants(id);
        """)
        print("  Done.")

    return True


def verify_migration(cur):
    """Verify the migration was successful."""
    print("\n[6/6] Verifying migration...")

    # Check tenants table
    cur.execute("SELECT id, name, slug FROM tenants WHERE id = 1;")
    tenant = cur.fetchone()
    if tenant:
        print(f"  Tenant: {tenant[1]} (slug: {tenant[2]})")
    else:
        print("  ERROR: ASFI tenant not found!")
        return False

    # Check config has required fields
    cur.execute("SELECT config FROM tenants WHERE id = 1;")
    config = cur.fetchone()[0]

    required_fields = ['businessTypes', 'leads']
    for field in required_fields:
        if field not in config:
            print(f"  ERROR: Config missing required field: {field}")
            return False

    if 'statuses' not in config.get('leads', {}):
        print("  ERROR: Config missing leads.statuses")
        return False

    if 'sources' not in config.get('leads', {}):
        print("  ERROR: Config missing leads.sources")
        return False

    print("  Config validation: PASSED")

    # Check lead_source column
    cur.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.columns
            WHERE table_name = 'leads' AND column_name = 'lead_source'
        );
    """)
    if cur.fetchone()[0]:
        print("  lead_source column: EXISTS")
    else:
        print("  ERROR: lead_source column missing!")
        return False

    # Check FK constraint
    cur.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.table_constraints
            WHERE constraint_name = 'fk_users_tenant'
        );
    """)
    if cur.fetchone()[0]:
        print("  users.tenant_id FK: EXISTS")
    else:
        print("  WARNING: users.tenant_id FK not created")

    print("\n" + "=" * 50)
    print("MIGRATION COMPLETED SUCCESSFULLY")
    print("=" * 50)
    return True


def main():
    print("=" * 50)
    print("ASFI DATABASE MIGRATION")
    print("=" * 50)
    print(f"Started at: {datetime.now().isoformat()}")

    conn = get_connection()
    cur = conn.cursor()

    try:
        if not check_prerequisites(cur):
            print("\nMigration cancelled.")
            return

        create_tenants_table(cur)
        insert_asfi_tenant(cur)
        add_lead_source_column(cur)

        if not add_users_foreign_key(cur):
            print("\nMigration incomplete - fix orphaned users and re-run.")
            conn.rollback()
            return

        if verify_migration(cur):
            response = input("\nCommit changes to database? (y/n): ").strip().lower()
            if response == 'y':
                conn.commit()
                print("\nChanges committed successfully!")
            else:
                conn.rollback()
                print("\nChanges rolled back.")
        else:
            conn.rollback()
            print("\nVerification failed. Changes rolled back.")

    except Exception as e:
        conn.rollback()
        print(f"\nERROR: {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
```

---

## Post-Migration Steps

After running the migration script:

1. **Deploy the new backend code** to the ASFI Fly.io app
   ```bash
   cd pathsix-backend
   flyctl deploy -a pathsix-backend  # Or whatever their app is named
   ```

2. **Update the frontend** to point to the backend (if needed)

3. **Test login** - User should receive tenant config in login response

4. **Test all pages**:
   - [ ] Dashboard loads
   - [ ] Leads page loads (uses `leads.statuses`, `leads.sources`, `businessTypes`)
   - [ ] Clients page loads (uses `businessTypes`)
   - [ ] Projects page loads (uses `businessTypes`)
   - [ ] Can create new Lead (form validation works)
   - [ ] Can create new Client
   - [ ] Can create new Project
   - [ ] Existing data displays correctly

5. **Verify reports** (if applicable) - Lead source reports should work with the new column

---

## Rollback Plan

If something goes wrong:

1. **If migration script fails mid-way**: The script uses transactions - changes are rolled back automatically

2. **If issues found after commit**: Restore from the backup you made before starting

3. **To remove the changes manually**:
   ```sql
   -- Remove FK constraint
   ALTER TABLE users DROP CONSTRAINT IF EXISTS fk_users_tenant;

   -- Remove lead_source column
   ALTER TABLE leads DROP COLUMN IF EXISTS lead_source;

   -- Remove tenants table (WARNING: loses config data)
   DROP TABLE IF EXISTS tenants CASCADE;
   ```

---

## Fly.io App Reference

**Old ASFI System** (from memory - verify these):
- Backend app: `pathsix-backend` (the OLD one, not `pathsixsolutions-backend`)
- Database app: `pathsix-db`
- Database name: `pathsix_backend`

**New PathSix System** (current development):
- Backend app: `pathsixsolutions-backend`
- Database app: `pathsixsolutions-db`
- Database name: `pathsixsolutions_backend`

---

## Notes for Future Claude Code Sessions

- The old ASFI database does NOT have a `tenants` table
- Users in old DB have `tenant_id` as plain integer (usually `1`)
- The `lead_source` column was added in the new version for reporting
- ASFI prefers "Account" instead of "Client" - this is in their tenant config
- The backup feature exists in code but is disabled (frontend commented out, scheduled machine destroyed)
- Always use `postgresql://` not `postgres://` when connecting with SQLAlchemy/psycopg2
- Container venv is at `/venv/bin/python`, NOT `/app/venv/`

---

*Last updated: 2026-02-05*
