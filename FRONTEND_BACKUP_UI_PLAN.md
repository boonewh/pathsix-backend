# Frontend Backup Management UI - Implementation Plan

## Overview

Admin-only UI for managing database backups and restores in your PathSix CRM frontend.

---

## 1. New Page/Route

### Location
Add a new admin-only route in your React router:

```
/admin/backups
```

### Access Control
- Only visible to users with `admin` role
- Protected route with role check
- Show in admin sidebar/navigation

---

## 2. Components to Create

### Main Page Component
**File**: `src/pages/admin/Backups.tsx` (or `.jsx`)

**Sections**:
1. **Page Header** - Title, description, "Create Backup" button
2. **Backup List** - Table/list of all backups with status
3. **Restore History** - Collapsible section showing past restores
4. **Loading States** - Skeletons/spinners while fetching data
5. **Error Handling** - Alert/toast for API errors

### Sub-Components

#### `BackupCard.tsx` (or list row)
Display individual backup with:
- Filename
- Type (manual, scheduled, pre_restore)
- Status badge (pending, in_progress, completed, failed)
- Created date/time
- Size (formatted MB/GB)
- Created by (user email)
- Actions: Restore, Delete, Download (future)

#### `CreateBackupButton.tsx`
- Button with confirmation modal
- "Are you sure?" message
- Shows estimated time warning
- Triggers POST to `/api/admin/backups`

#### `RestoreConfirmationModal.tsx`
- **Critical**: Multi-step confirmation
- Warning about destructive operation
- Shows which backup will be restored
- Shows pre-restore safety backup will be created
- Requires typing "RESTORE" to confirm
- Triggers POST to `/api/admin/backups/:id/restore`

#### `BackupStatusBadge.tsx`
Status indicator with colors:
- `pending` - Yellow/orange (⏳)
- `in_progress` - Blue with spinner (🔄)
- `completed` - Green (✅)
- `failed` - Red (❌)

#### `RestoreHistoryTable.tsx`
Table showing:
- Restore date/time
- Which backup was restored
- Restored by (user)
- Pre-restore safety backup link
- Status
- Error message (if failed)

---

## 3. API Integration

### API Service File
**File**: `src/services/backupApi.ts` (or `.js`)

```typescript
// Example implementation
const API_BASE = '/api/admin/backups';

export const backupApi = {
  // List all backups
  listBackups: async (params?: { limit?: number; offset?: number; status?: string }) => {
    const query = new URLSearchParams(params as any);
    const response = await fetch(`${API_BASE}?${query}`, {
      headers: {
        'Authorization': `Bearer ${getAuthToken()}`,
      },
    });
    return response.json();
  },

  // Create manual backup
  createBackup: async () => {
    const response = await fetch(API_BASE, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${getAuthToken()}`,
        'Content-Type': 'application/json',
      },
    });
    return response.json();
  },

  // Get backup status
  getBackupStatus: async (backupId: number) => {
    const response = await fetch(`${API_BASE}/${backupId}/status`, {
      headers: {
        'Authorization': `Bearer ${getAuthToken()}`,
      },
    });
    return response.json();
  },

  // Restore from backup
  restoreBackup: async (backupId: number) => {
    const response = await fetch(`${API_BASE}/${backupId}/restore`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${getAuthToken()}`,
        'Content-Type': 'application/json',
      },
    });
    return response.json();
  },

  // Delete backup
  deleteBackup: async (backupId: number) => {
    const response = await fetch(`${API_BASE}/${backupId}`, {
      method: 'DELETE',
      headers: {
        'Authorization': `Bearer ${getAuthToken()}`,
      },
    });
    return response.json();
  },

  // List restore history
  listRestores: async (params?: { limit?: number; offset?: number }) => {
    const query = new URLSearchParams(params as any);
    const response = await fetch(`${API_BASE}/restores?${query}`, {
      headers: {
        'Authorization': `Bearer ${getAuthToken()}`,
      },
    });
    return response.json();
  },
};
```

---

## 4. UI/UX Design

### Page Layout (Desktop)

```
┌─────────────────────────────────────────────────────────────┐
│ 🔒 Database Backups                    [+ Create Backup]    │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│ Manage production database backups. All backups are         │
│ encrypted and stored securely in Backblaze B2.              │
│                                                              │
├─────────────────────────────────────────────────────────────┤
│ Filters:  [All ▼] [Status: All ▼]          🔍 Search        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│ ┌──────────────────────────────────────────────────────┐   │
│ │ ✅ backup_manual_20251222_143022.dump.gpg            │   │
│ │ Manual backup • 487 MB                                │   │
│ │ Created Dec 22, 2025 2:30 PM by admin@example.com    │   │
│ │ Checksum: a3f5d9... [Restore] [Delete]               │   │
│ └──────────────────────────────────────────────────────┘   │
│                                                              │
│ ┌──────────────────────────────────────────────────────┐   │
│ │ 🔄 backup_scheduled_20251221_020000.dump.gpg         │   │
│ │ Scheduled backup • In Progress...                     │   │
│ │ Created Dec 21, 2025 2:00 AM by system               │   │
│ │ [View Progress]                                       │   │
│ └──────────────────────────────────────────────────────┘   │
│                                                              │
│ [Load More]                                                 │
│                                                              │
├─────────────────────────────────────────────────────────────┤
│ ▼ Restore History (3)                                       │
├─────────────────────────────────────────────────────────────┤
│ │ Date           │ Backup                │ By      │ Status│
│ │ Dec 20, 3:15pm │ backup_manual_...     │ admin@  │ ✅    │
│ │ Dec 15, 1:22pm │ backup_scheduled_...  │ admin@  │ ✅    │
│ │ Dec 10, 9:45am │ backup_manual_...     │ admin@  │ ❌    │
└─────────────────────────────────────────────────────────────┘
```

### Restore Confirmation Modal

```
┌────────────────────────────────────────────────────┐
│  ⚠️  Restore Database from Backup                 │
├────────────────────────────────────────────────────┤
│                                                     │
│  WARNING: This is a DESTRUCTIVE operation!         │
│                                                     │
│  This will:                                         │
│  1. Create a safety backup of current database     │
│  2. Replace your database with this backup:        │
│                                                     │
│     backup_manual_20251220_150000.dump.gpg         │
│     Created: Dec 20, 2025 3:00 PM                  │
│     Size: 487 MB                                   │
│                                                     │
│  All data created after this backup will be lost.  │
│                                                     │
│  Type RESTORE to confirm:                          │
│  ┌───────────────────────────────────────────┐    │
│  │ [_________________________________]        │    │
│  └───────────────────────────────────────────┘    │
│                                                     │
│           [Cancel]  [Restore Database]             │
│                     (disabled until typed)         │
└────────────────────────────────────────────────────┘
```

---

## 5. State Management

### Using React State (if simple)

```typescript
const [backups, setBackups] = useState([]);
const [restores, setRestores] = useState([]);
const [loading, setLoading] = useState(true);
const [error, setError] = useState(null);
const [selectedBackup, setSelectedBackup] = useState(null);
const [showRestoreModal, setShowRestoreModal] = useState(false);
```

### Using React Query (recommended)

```typescript
// Fetch backups with auto-refresh
const { data: backupsData, isLoading, error, refetch } = useQuery({
  queryKey: ['backups'],
  queryFn: () => backupApi.listBackups(),
  refetchInterval: 10000, // Poll every 10s for status updates
});

// Create backup mutation
const createBackupMutation = useMutation({
  mutationFn: backupApi.createBackup,
  onSuccess: () => {
    toast.success('Backup created successfully');
    refetch(); // Refresh list
  },
  onError: (error) => {
    toast.error('Failed to create backup');
  },
});
```

---

## 6. Polling for Status Updates

Since backups can take time, implement polling:

```typescript
// Poll for in-progress backups
useEffect(() => {
  const inProgressBackups = backups.filter(
    b => b.status === 'pending' || b.status === 'in_progress'
  );

  if (inProgressBackups.length > 0) {
    const interval = setInterval(() => {
      refetchBackups();
    }, 5000); // Poll every 5 seconds

    return () => clearInterval(interval);
  }
}, [backups]);
```

---

## 7. Features Checklist

### Phase 1: Basic Functionality (MVP)
- [ ] Create backup button
- [ ] List all backups
- [ ] Show backup status badges
- [ ] Format dates/sizes nicely
- [ ] Delete backup (with confirmation)
- [ ] Basic error handling

### Phase 2: Restore
- [ ] Restore button on each backup
- [ ] Multi-step confirmation modal
- [ ] Type "RESTORE" to confirm
- [ ] Show restore progress
- [ ] Handle restore errors

### Phase 3: Polish
- [ ] Auto-refresh for in-progress backups
- [ ] Restore history section
- [ ] Search/filter backups
- [ ] Pagination
- [ ] Empty states
- [ ] Loading skeletons
- [ ] Toast notifications

### Phase 4: Advanced (Optional)
- [ ] Download backup file (if B2 URLs are pre-signed)
- [ ] Backup size trends chart
- [ ] Email notification preferences
- [ ] Scheduled backup configuration UI
- [ ] Retention policy settings

---

## 8. Error Handling

### Common Errors to Handle

```typescript
// Network errors
if (!response.ok) {
  if (response.status === 401) {
    // Unauthorized - redirect to login
  } else if (response.status === 403) {
    // Forbidden - not admin
    toast.error('Admin access required');
  } else if (response.status === 404) {
    // Backup not found
    toast.error('Backup not found');
  } else if (response.status === 400) {
    // Bad request (e.g., can't restore in-progress backup)
    const data = await response.json();
    toast.error(data.error);
  } else {
    toast.error('Something went wrong');
  }
}
```

### User-Friendly Messages

- Creating backup: "Creating backup... This may take a few minutes."
- Restore in progress: "Restoring database... Please do not close this window."
- Failed backup: "Backup failed: [error message]. Please try again."
- Restore failed: "Restore failed: [error message]. Your database is unchanged."

---

## 9. Styling Recommendations

### Status Colors
- **Pending**: `bg-yellow-100 text-yellow-800` (Tailwind)
- **In Progress**: `bg-blue-100 text-blue-800` with spinner
- **Completed**: `bg-green-100 text-green-800`
- **Failed**: `bg-red-100 text-red-800`

### Button Styles
- **Create Backup**: Primary button (blue)
- **Restore**: Warning button (orange/yellow)
- **Delete**: Danger button (red)

### Icons (if using React Icons)
```typescript
import {
  FiDownload,      // Create backup
  FiUpload,        // Restore
  FiTrash2,        // Delete
  FiRefreshCw,     // In progress spinner
  FiCheckCircle,   // Completed
  FiAlertCircle,   // Failed
  FiClock,         // Pending
} from 'react-icons/fi';
```

---

## 10. TypeScript Types

```typescript
// types/backup.ts
export interface Backup {
  id: number;
  filename: string;
  type: 'manual' | 'scheduled' | 'pre_restore';
  status: 'pending' | 'in_progress' | 'completed' | 'failed';
  size: number; // bytes
  database_size: number | null; // bytes
  checksum: string | null;
  created_at: string; // ISO date
  completed_at: string | null; // ISO date
  created_by: string | null; // email
  error: string | null;
}

export interface BackupRestore {
  id: number;
  backup_id: number;
  pre_restore_backup_id: number | null;
  restored_by: string; // email
  status: 'in_progress' | 'completed' | 'failed';
  started_at: string; // ISO date
  completed_at: string | null; // ISO date
  error: string | null;
}

export interface BackupListResponse {
  backups: Backup[];
  total: number;
  limit: number;
  offset: number;
}

export interface RestoreListResponse {
  restores: BackupRestore[];
  total: number;
  limit: number;
  offset: number;
}
```

---

## 11. Utility Functions

```typescript
// utils/formatters.ts

export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

export function formatDate(isoDate: string): string {
  return new Date(isoDate).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

export function getBackupTypeLabel(type: string): string {
  const labels = {
    manual: 'Manual Backup',
    scheduled: 'Scheduled Backup',
    pre_restore: 'Pre-Restore Safety Backup',
  };
  return labels[type] || type;
}
```

---

## 12. Testing

### Manual Testing Checklist
- [ ] Create a manual backup
- [ ] Verify backup appears in list with "pending" status
- [ ] Wait for backup to complete (or refresh)
- [ ] Verify status changes to "completed"
- [ ] Click "Restore" on a backup
- [ ] Verify scary confirmation modal appears
- [ ] Type "RESTORE" and confirm
- [ ] Verify pre-restore safety backup is created
- [ ] Verify restore completes
- [ ] Check restore history section
- [ ] Delete a backup
- [ ] Verify backup is removed from list and B2

### Error Cases to Test
- [ ] Try to restore from an in-progress backup (should fail)
- [ ] Try to restore with invalid backup ID (should 404)
- [ ] Try to access page as non-admin (should redirect/error)
- [ ] Create backup while one is already running
- [ ] Network timeout during backup creation

---

## 13. Example Component Structure

```
src/
├── pages/
│   └── admin/
│       └── Backups.tsx              # Main page
├── components/
│   └── admin/
│       └── backups/
│           ├── BackupCard.tsx       # Individual backup display
│           ├── BackupList.tsx       # List container
│           ├── BackupStatusBadge.tsx
│           ├── CreateBackupButton.tsx
│           ├── RestoreConfirmationModal.tsx
│           └── RestoreHistoryTable.tsx
├── services/
│   └── backupApi.ts                 # API calls
├── types/
│   └── backup.ts                    # TypeScript types
└── utils/
    └── formatters.ts                # Helper functions
```

---

## 14. Navigation Updates

Add to your admin navigation menu:

```typescript
// In your sidebar/nav component
{hasRole('admin') && (
  <NavLink to="/admin/backups">
    <FiDatabase /> Database Backups
  </NavLink>
)}
```

---

## 15. Security Considerations

- ✅ All API calls require JWT with `admin` role
- ✅ Restore requires explicit typed confirmation ("RESTORE")
- ✅ Show warning about data loss
- ✅ No backup file download URLs exposed (unless you add pre-signed URLs later)
- ✅ Log all restore operations for audit trail

---

## 16. Mobile Responsiveness

### Adjustments for Mobile
- Stack backup info vertically instead of horizontal
- Make action buttons full-width
- Simplify restore history table (maybe cards instead)
- Reduce font sizes
- Hide less critical info (checksums, technical details)

---

## Next Steps

1. **Create the base page** - Start with `Backups.tsx`
2. **Add API service** - Implement `backupApi.ts`
3. **Build backup list** - Simple table/card layout
4. **Add create button** - With basic confirmation
5. **Implement status polling** - Auto-refresh for in-progress backups
6. **Add restore flow** - Multi-step confirmation modal
7. **Polish UI** - Loading states, errors, formatting
8. **Test thoroughly** - Especially the restore flow

---

## Questions?

This plan provides a complete admin UI for your backup system. Start with the MVP features and add polish iteratively. The most critical part is the **restore confirmation flow** - make it very clear this is destructive!