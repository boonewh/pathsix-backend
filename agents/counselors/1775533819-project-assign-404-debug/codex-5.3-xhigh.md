**Findings**
1. `HIGH` Inconsistent soft-delete semantics are the direct cause of the 404.
- `get_project` loads by `id + tenant_id` and does **not** filter `deleted_at` ([projects.py#L143](C:/Users/William%20Boone/Desktop/Websites/pathsix-backend/app/routes/projects.py#L143)).
- `assign_project` adds `Project.deleted_at == None` ([projects.py#L313](C:/Users/William%20Boone/Desktop/Websites/pathsix-backend/app/routes/projects.py#L313), [projects.py#L329](C:/Users/William%20Boone/Desktop/Websites/pathsix-backend/app/routes/projects.py#L329)).
- Result: a soft-deleted project can load in detail view, then assignment returns `404 Project not found`.

2. `HIGH` Soft-deleted projects are exposed in multiple “active” project paths, not just detail.
- List query lacks `deleted_at == None` ([projects.py#L32](C:/Users/William%20Boone/Desktop/Websites/pathsix-backend/app/routes/projects.py#L32), [projects.py#L48](C:/Users/William%20Boone/Desktop/Websites/pathsix-backend/app/routes/projects.py#L48)).
- Admin list also lacks it ([projects.py#L401](C:/Users/William%20Boone/Desktop/Websites/pathsix-backend/app/routes/projects.py#L401), [projects.py#L422](C:/Users/William%20Boone/Desktop/Websites/pathsix-backend/app/routes/projects.py#L422)).
- Update path lacks it too ([projects.py#L254](C:/Users/William%20Boone/Desktop/Websites/pathsix-backend/app/routes/projects.py#L254), [projects.py#L272](C:/Users/William%20Boone/Desktop/Websites/pathsix-backend/app/routes/projects.py#L272)).
- This is why users can keep interacting with deleted records until they hit a stricter endpoint like `/assign`.

3. `MEDIUM` Migration graph has branch risk (separate issue, but worth fixing).
- `add_project_assigned_to` and `add_backup_tables` both point to `add_lead_source_field` ([add_project_assigned_to.py#L14](C:/Users/William%20Boone/Desktop/Websites/pathsix-backend/migrations/versions/add_project_assigned_to.py#L14), [add_backup_tables.py#L14](C:/Users/William%20Boone/Desktop/Websites/pathsix-backend/migrations/versions/add_backup_tables.py#L14)).
- `add_tenants_table` continues only from backup branch ([add_tenants_table.py#L20](C:/Users/William%20Boone/Desktop/Websites/pathsix-backend/migrations/versions/add_tenants_table.py#L20)).
- This can create environment drift if heads aren’t merged explicitly.

**Answers to your 4 questions**
1. Yes. That is the root cause.
2. No. This is not a Quart/Werkzeug route collision. `/<int:project_id>` cannot swallow `/123/assign`, and static routes like `/all` or `/trash` are not matched by `<int>`.
3. Very unlikely. If `deleted_at` were missing in DB, SQLAlchemy would throw and you’d hit 500, not the explicit 404 branch. Also, `ix_projects_deleted_at` migration implies the column existed when that migration ran ([add_performance_indexes.py#L47](C:/Users/William%20Boone/Desktop/Websites/pathsix-backend/migrations/versions/add_performance_indexes.py#L47)).
4. Right fix: keep the `assign_project` deleted filter, and make all non-trash project read/update endpoints consistently exclude soft-deleted rows.

**Minimal fix (recommended)**
1. Add `Project.deleted_at.is_(None)` to:
- `get_project`
- `list_projects`
- `list_all_projects`
- `list_projects_by_client`
- `list_projects_by_lead`
- `update_project`
2. Keep `/assign` strict.
3. Optionally improve `/assign` UX: if `id+tenant` exists but `deleted_at` is not null, return `409` with “Project is deleted; restore first” instead of generic 404.

If you want, I can prepare the exact patch, but this workspace is currently read-only.
