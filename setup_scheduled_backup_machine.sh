#!/bin/bash
# Setup script for Fly.io scheduled backup machine
# This creates a scheduled machine that runs daily backups

set -e  # Exit on error

APP_NAME="pathsixsolutions-backend"
IMAGE="registry.fly.io/pathsixsolutions-backend:deployment-01KDGFQGEES1EXV922FQH0KJ73"
MACHINE_ID="9080736df74308"

echo "========================================"
echo "Fly.io Scheduled Backup Setup"
echo "========================================"

# Step 1: Destroy existing machine if it exists
echo ""
echo "Step 1: Destroying existing machine ${MACHINE_ID}..."
if flyctl machine destroy ${MACHINE_ID} --app ${APP_NAME} --force 2>/dev/null; then
    echo "✓ Machine destroyed successfully"
else
    echo "! Machine may not exist or already destroyed (continuing...)"
fi

# Wait a moment for cleanup
sleep 2

# Step 2: Create scheduled machine with proper configuration
echo ""
echo "Step 2: Creating new scheduled backup machine..."
echo "Image: ${IMAGE}"
echo "Schedule: daily"
echo "Region: ord"
echo "Memory: 512MB"
echo ""

# Pass command as positional args after the image (NOT as flags)
flyctl machine run ${IMAGE} \
    python scripts/run_scheduled_backup.py \
    --app ${APP_NAME} \
    --name scheduled-backup \
    --region ord \
    --schedule daily \
    --restart no \
    --vm-memory 512 \
    --vm-cpus 1 \
    --vm-cpu-kind shared

echo ""
echo "========================================"
echo "✓ Setup complete!"
echo "========================================"
echo ""
echo "The backup machine will:"
echo "  - Run python scripts/run_scheduled_backup.py"
echo "  - Execute once daily (fuzzy schedule)"
echo "  - Exit after completion"
echo "  - Not restart automatically"
echo ""
echo "To view machine status:"
echo "  flyctl machine list --app ${APP_NAME}"
echo ""
echo "To check backup logs:"
echo "  flyctl logs --app ${APP_NAME}"
echo ""
echo "To manually trigger a backup:"
echo "  flyctl machine start <MACHINE_ID> --app ${APP_NAME}"
echo ""
