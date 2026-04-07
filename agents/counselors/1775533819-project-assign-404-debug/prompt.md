# Review Request

## Question

Multi-tenant CRM (Quart/SQLAlchemy backend, React/TypeScript frontend). We just added an `assigned_to` column to the `projects` table and a `PUT /api/projects/{id}/assign` endpoint. The migration ran successfully. The project detail page loads fine, but clicking "Assign" returns a 404 ("Project not found").

**Core finding:** The only meaningful difference between `get_project` (works) and `assign_project` (404) is that `assign_project` includes `Project.deleted_at == None` in its filter.

`get_project` query (works):
```python
session.query(Project).filter(
    Project.id == project_id,
    Project.tenant_id == user.tenant_id
).first()
```

`assign_project` query (404):
```python
session.query(Project).filter(
    Project.id == project_id,
    Project.tenant_id == user.tenant_id,
    Project.deleted_at == None   # ← only extra filter
).first()
```

**Known facts:**
- `Project` model has `deleted_at = Column(DateTime, nullable=True)` at line 239 of `app/models.py` — column exists in the model
- `Project` model has `assigned_to = Column(Integer, ForeignKey('users.id'), nullable=True)` at line 241 — new column, migration ran
- `ProjectAssignSchema` is `assigned_to: int = Field(..., gt=0)` — trivially correct
- `requires_auth(roles=["admin"])` returns 403 for non-admins — user IS admin (they see the Assign button which is gated on `userHasRole(user, "admin")`)
- Frontend call: `apiFetch('/projects/${project.id}/assign', { method: 'PUT', body: JSON.stringify({ assigned_to: selectedUserId }) })` — looks correct
- The outer `except Exception` block returns 500, not 404 — so we're NOT hitting a Python/SQLAlchemy exception; we're hitting the `if not project: return 404` branch
- `get_project` does NOT filter on `deleted_at`, meaning a soft-deleted project could load its detail page but fail to assign

**The questions:**
1. Is the root cause that `get_project` doesn't filter on `deleted_at`, so a soft-deleted project's page loads but `assign_project` (correctly) rejects it?
2. Could there be a Quart/Werkzeug route matching issue where `PUT /api/projects/123/assign` is being intercepted by a different route?
3. Could the `deleted_at` column exist in the SQLAlchemy model but NOT in the actual PostgreSQL database (migration ran for `assigned_to`, but was `deleted_at` always there)?
4. What is the right fix?

## Context

### Files to Review

@app/routes/projects.py
@app/models.py
@app/schemas/projects.py
@app/utils/auth_utils.py

### Frontend
@c:/Users/William Boone/Desktop/Websites/pathsix-crm-custom/src/pages/ProjectDetailPage.tsx

### Recent Changes
The recent migration added `assigned_to` column to `projects` table. Run `git log --oneline -5` to see recent commits.

## Instructions

You are providing an independent review. Be critical and thorough.

- Read the referenced files to understand the full context
- The assign route is at lines 313–368 of `app/routes/projects.py`
- Look at ALL routes in `app/routes/projects.py` — check for route registration order issues (e.g. static routes like `/all`, `/trash` registered after `/<int:project_id>` — does Quart/Werkzeug handle this correctly?)
- Check whether `deleted_at` on the `Project` model is likely to be in the actual DB or just the model (look at alembic migrations if you can find them)
- Think about whether the `deleted_at == None` filter could produce a false negative (e.g. if the column has a non-NULL default in Postgres)
- Propose the minimal fix that makes the assign route work reliably
- Note any secondary bugs (e.g. `get_project` showing soft-deleted projects)
- Be direct and opinionated — don't hedge
