# Database Backup & Restore System

Complete guide for managing PathSix CRM database backups and restores.

---

## Quick Reference

**Create Backup (API):**
```bash
curl -X POST https://your-app.fly.dev/api/admin/backups \
  -H "Authorization: Bearer $TOKEN"
```

**List Backups:**
```bash
curl https://your-app.fly.dev/api/admin/backups \
  -H "Authorization: Bearer $TOKEN"
```

**Restore from Backup:**
```bash
curl -X POST https://your-app.fly.dev/api/admin/backups/:id/restore \
  -H "Authorization: Bearer $TOKEN"
```

---

## System Overview

The backup system provides:
- ✅ Automated daily backups via Fly.io scheduled machine
- ✅ Manual on-demand backups via API
- ✅ GPG AES256 encryption
- ✅ Backblaze B2 storage
- ✅ Automatic pre-restore safety backups
- ✅ SHA-256 checksum verification
- ✅ 30-day retention with automatic cleanup
- ✅ Permanent restore audit trail in B2

---

## Scheduled Backups

### Current Setup

A Fly.io scheduled machine runs daily:
- **Machine ID:** Check with `flyctl machine list --app pathsixsolutions-backend`
- **Schedule:** Daily (fuzzy timing within 24-hour window)
- **Command:** `python scripts/run_scheduled_backup.py`
- **Auto-restart:** No (exits after completion)

### Verify Scheduled Backups

```bash
# List machines
flyctl machine list --app pathsixsolutions-backend

# View logs
flyctl logs --app pathsixsolutions-backend

# Manual trigger (for testing)
flyctl machine start <MACHINE_ID> --app pathsixsolutions-backend
```

### Recreate Scheduled Machine

If you need to recreate the scheduled backup machine:

```bash
bash setup_scheduled_backup_machine.sh
```

This script will:
1. Destroy the existing backup machine
2. Create a new one with the correct configuration
3. Set it to run daily

---

## API Endpoints

All endpoints require `admin` role.

### List Backups
```
GET /api/admin/backups?limit=50&offset=0
```

**Response:**
```json
{
  "backups": [
    {
      "id": 5,
      "filename": "backup_scheduled_20251227_183922.dump.gpg",
      "type": "scheduled",
      "status": "completed",
      "size": 9106,
      "checksum": "3b22e74f19c41a7be3e3179a440d36feff71519becb4a9a8af98759dcdced6c7",
      "created_at": "2025-12-27T18:39:22Z",
      "completed_at": "2025-12-27T18:39:25Z"
    }
  ],
  "total": 5,
  "limit": 50,
  "offset": 0
}
```

### Create Manual Backup
```
POST /api/admin/backups
```

**Response:**
```json
{
  "id": 6,
  "message": "Backup started",
  "status": "pending"
}
```

### Get Backup Status
```
GET /api/admin/backups/:id/status
```

**Response:**
```json
{
  "id": 5,
  "status": "completed",
  "filename": "backup_scheduled_20251227_183922.dump.gpg",
  "size": 9106,
  "created_at": "2025-12-27T18:39:22Z",
  "completed_at": "2025-12-27T18:39:25Z"
}
```

### Restore from Backup
```
POST /api/admin/backups/:id/restore
```

**⚠️ DESTRUCTIVE OPERATION** - Creates pre-restore safety backup automatically

**Response:**
```json
{
  "message": "Restore initiated",
  "restore_id": 1,
  "pre_restore_backup_id": 7
}
```

### Delete Backup
```
DELETE /api/admin/backups/:id
```

Removes backup from database and B2 storage.

### List Restore History
```
GET /api/admin/backups/restores?limit=50&offset=0
```

**Response:**
```json
{
  "restores": [
    {
      "restore_id": 1,
      "restore_date": "2025-12-27T20:15:00Z",
      "user_email": "admin@demo.com",
      "backup_restored": "backup_manual_20251227_120000.dump.gpg",
      "backup_id": 3,
      "backup_date": "2025-12-27T12:00:00Z",
      "safety_backup_created": "pre_restore_20251227_201500.sql.gpg",
      "safety_backup_id": 8
    }
  ],
  "total": 1
}
```

---

## How It Works

### Backup Process

1. Job creates `Backup` record with status `pending`
2. Runs `pg_dump -Fc` (PostgreSQL custom format)
3. Encrypts with GPG AES256
4. Calculates SHA-256 checksum
5. Uploads to B2 at `backups/prod/YYYY/MM/filename`
6. Updates record to `completed`
7. Cleans up local temp files

**Storage Path Example:**
```
backups/prod/2025/12/backup_scheduled_20251227_183922.dump.gpg
```

### Restore Process

1. Creates pre-restore safety backup automatically
2. Downloads encrypted backup from B2
3. Verifies SHA-256 checksum
4. Decrypts with GPG
5. Runs `pg_restore --clean --if-exists`
6. Logs restore metadata to B2 (survives database wipe)
7. Updates restore status
8. Cleans up temp files

**Important:** Restore logs are stored in B2 at `restore_logs/YYYY/MM/restore_YYYYMMDD_HHMMSS.json` to provide a permanent audit trail that survives database restores.

---

## Configuration

Environment variables (set via `flyctl secrets set`):

```bash
# B2 Storage
BACKUP_S3_ENDPOINT_URL=https://s3.us-east-005.backblazeb2.com
BACKUP_S3_REGION=us-east-005
BACKUP_S3_ACCESS_KEY_ID=your_key
BACKUP_S3_SECRET_ACCESS_KEY=your_secret
BACKUP_S3_BUCKET=pathsixdesigns-vault

# Encryption (CRITICAL - Keep safe!)
BACKUP_GPG_PASSPHRASE=your-secure-passphrase

# Retention & Timeouts
BACKUP_RETENTION_DAYS=30
BACKUP_JOB_TIMEOUT_MINUTES=60
```

---

## Troubleshooting

### Backup Failed

**Check logs:**
```bash
flyctl logs --app pathsixsolutions-backend | grep -i backup
```

**Common issues:**
- B2 credentials incorrect → Verify `BACKUP_S3_*` secrets
- GPG passphrase missing → Verify `BACKUP_GPG_PASSPHRASE` secret
- Database connection failed → Check `DATABASE_URL`
- Out of disk space → Machine needs more storage

### Restore Failed

**Check what happened:**
```bash
flyctl logs --app pathsixsolutions-backend | grep -i restore
```

**Common issues:**
- Checksum mismatch → Backup file corrupted, use different backup
- GPG decryption failed → Wrong passphrase or corrupted file
- pg_restore errors → Database version mismatch or permissions

### Scheduled Backups Not Running

```bash
# Check machine status
flyctl machine list --app pathsixsolutions-backend

# Check if machine is stopped
flyctl machine status <MACHINE_ID>

# Manually start to test
flyctl machine start <MACHINE_ID>
```

---

## Security

1. **GPG Passphrase:** Stored as encrypted Fly.io secret
2. **B2 Bucket:** Private (not public)
3. **API Access:** Admin role required via JWT
4. **Pre-Restore Backups:** Every restore creates safety backup first
5. **Checksums:** SHA-256 verification for data integrity
6. **Audit Trail:** All restores logged permanently to B2

---

## Cost (Backblaze B2)

Assuming:
- Database size: 500 MB compressed
- Daily backups with 30-day retention
- 1 restore/month

**Monthly Cost: ~$0.08**

Breakdown:
- Storage: 30 × 500 MB = 15 GB @ $0.005/GB = $0.08
- Downloads: Free (first 1 GB/day)
- API calls: Free (under daily limits)

---

## Frontend Implementation

See complete frontend UI plan in the original `FRONTEND_BACKUP_UI_PLAN.md` if needed. Key points:

### Admin Page: `/admin/backups`

**Features to implement:**
- List all backups with status badges
- Create manual backup button
- Restore button with multi-step confirmation
- Delete backup (with confirmation)
- Restore history table
- Auto-refresh for in-progress backups

**Restore Confirmation:**
```
⚠️ WARNING: This is a DESTRUCTIVE operation!

Type RESTORE to confirm:
[____________________]

[Cancel]  [Restore Database]
```

---

## Monitoring

### Check Backup Status

```bash
# Recent backups
curl https://your-app.fly.dev/api/admin/backups \
  -H "Authorization: Bearer $TOKEN" \
  | jq '.backups[] | {filename, status, created_at}'
```

### Check Restore History

```bash
# Recent restores
curl https://your-app.fly.dev/api/admin/backups/restores \
  -H "Authorization: Bearer $TOKEN" \
  | jq '.restores[] | {restore_date, user_email, backup_restored}'
```

### Scheduled Machine Health

```bash
# View last run
flyctl logs --app pathsixsolutions-backend -i <MACHINE_ID> | tail -50
```

---

## Files Reference

- `scripts/run_scheduled_backup.py` - Scheduled backup script
- `scripts/cleanup_backups.py` - Retention cleanup (run weekly)
- `app/routes/admin_backups.py` - API endpoints
- `app/utils/backup_storage.py` - B2 storage backend
- `setup_scheduled_backup_machine.sh` - Machine setup script

---

## Support

For issues:
1. Check logs: `flyctl logs --app pathsixsolutions-backend`
2. Verify secrets are set: `flyctl secrets list --app pathsixsolutions-backend`
3. Test manual backup via API
4. Review this guide's troubleshooting section
