RULES FOR IMPLEMENTATION

These are mandatory rules to follow as we implement changes:

1. The Backend Is the Source of Truth for Validation

Frontend Zod schemas cannot invent or relax rules.

Backend → Pydantic defines truth
Frontend → Zod mirrors it

This avoids:

Drift

Mixed rules

Bugs that happen only on one side

Instruction:

For any schema change, update Pydantic first, then Zod to match.

2. The Backend Must Never Assume a Specific Frontend

Because multiple tenants will hit this backend, the backend must:

Never assume specific UI components

Never expect specific enumeration labels from a single frontend

Never embed any tenant-specific logic

Instruction:

The backend must remain generic and tenant-agnostic.
All tenant overrides happen in frontend config or custom backends, not the shared backend.

3. All API responses must remain stable and schema-versioned

Since different frontends (versions) may hit the same backend:

Keep response fields stable

If you need breaking changes, add versioned endpoints:

/api/v1/...

/api/v2/...

Instruction:

Validation changes must NOT break the existing response shape unless creating a versioned endpoint.

4. Status values must be standardized now (as you planned)

Statuses are set, they will not be changed. 

Instruction:

These are the ONLY validated statuses in the generic backend.
Custom statuses live in a custom backend later.

This avoids chaos later when a tenant wants weird custom statuses.

5. The Backend Should Enforce tenant_id Everywhere1. The Backend Is the Source of Truth for Validation

Frontend Zod schemas cannot invent or relax rules.

Backend → Pydantic defines truth
Frontend → Zod mirrors it

This avoids:

Drift

Mixed rules

Bugs that happen only on one side

Instruction:

For any schema change, update Pydantic first, then Zod to match.

2. The Backend Must Never Assume a Specific Frontend

Because multiple tenants will hit this backend, the backend must:

Never assume specific UI components

Never expect specific enumeration labels from a single frontend

Never embed any tenant-specific logic

Instruction:

The backend must remain generic and tenant-agnostic.
All tenant overrides happen in frontend config or custom backends, not the shared backend.

3. All API responses must remain stable and schema-versioned

Since different frontends (versions) may hit the same backend:

Keep response fields stable

If you need breaking changes, add versioned endpoints:

/api/v1/...

/api/v2/...

Instruction:

Validation changes must NOT break the existing response shape unless creating a versioned endpoint.

4. Status values must be standardized now (as you planned)

You already nailed this:

PROJECT_STATUS_OPTIONS = ["active", "pending", "completed", "cancelled"]
CLIENT_STATUS_OPTIONS  = ["prospect", "active", "inactive", "cancelled"]
LEAD_STATUS_OPTIONS    = ["new", "contacted", "qualified", "lost", "converted"]


Instruction:

These are the ONLY validated statuses in the generic backend.
Custom statuses live in a custom backend later.

This avoids chaos later when a tenant wants weird custom statuses.

5. The Backend Should Enforce tenant_id Everywhere

Even with separated front/back, do NOT loosen this.

You MUST enforce:

Every query must be scoped by tenant_id.

Every insert/update must include tenant_id.

Every list view must filter by tenant_id.

Instruction:

The backend is multi-tenant by default and must enforce tenant boundaries in every resolver.

6. The Frontend Must Not Hardcode Backend URLs

You’re going to serve multiple frontend variants per tenant later.

Set:

VITE_API_URL=http://localhost:5000


Then in code:

const API_URL = import.meta.env.VITE_API_URL;


Instruction:

All frontends use environment variables for backend URLs so each tenant can point to the right backend instance.

7. When Doing Validation: Add Zod + Pydantic At the Same Time

You’re starting Step 1.
Here’s the safe process:

Step 1A — Define Pydantic schemas

LeadCreateSchema

LeadUpdateSchema

ClientCreateSchema

ProjectUpdateSchema
…etc.

Step 1B — Create matching Zod schemas

You literally mirror Pydantic with Zod.

Step 1C — Add runtime backend validation (reject bad payloads)
Step 1D — Update frontend forms to use Zod validation before submit

Instruction:

Validation step must update BOTH front and back.
No single-sided validation passes.

8. No Tenant-Specific Logic Allowed in the Generic Backend

If a tenant eventually needs:

custom statuses

custom workflows

custom fields

They get:

Their own backend

Their own database

Their own deployment

Instruction:

The generic backend must stay generic.
All “special cases” belong in a dedicated backend.

9. Errors Must Be Standardized

You want:

{
  "error": "Invalid lead status",
  "details": { ... }
}


Not a dozen shapes.

Instruction:

Backend errors must always follow one structure so all frontends can handle them.

10. Document All Schemas in a “Contracts” Folder

This saves AI agents (and you) tons of time.

Add:

/contracts  
  lead.json  
  client.json  
  project.json  
  user.json  
  shared_types.json  


These documents define:

expected fields

enums

validation rules

required/optional

Instruction:

Keep a /contracts folder with all API schema definitions as the canonical handshake between front and back.

Even with separated front/back, do NOT loosen this.

You MUST enforce:

Every query must be scoped by tenant_id.

Every insert/update must include tenant_id.

Every list view must filter by tenant_id.

Instruction:

The backend is multi-tenant by default and must enforce tenant boundaries in every resolver.

6. The Frontend Must Not Hardcode Backend URLs

You’re going to serve multiple frontend variants per tenant later.

Set:

VITE_API_URL=http://localhost:5000


Then in code:

const API_URL = import.meta.env.VITE_API_URL;


Instruction:

All frontends use environment variables for backend URLs so each tenant can point to the right backend instance.

7. When Doing Validation: Add Zod + Pydantic At the Same Time

You’re starting Step 1.
Here’s the safe process:

Step 1A — Define Pydantic schemas

LeadCreateSchema

LeadUpdateSchema

ClientCreateSchema

ProjectUpdateSchema
…etc.

Step 1B — Create matching Zod schemas

You literally mirror Pydantic with Zod.

Step 1C — Add runtime backend validation (reject bad payloads)
Step 1D — Update frontend forms to use Zod validation before submit

Instruction:

Validation step must update BOTH front and back.
No single-sided validation passes.

8. No Tenant-Specific Logic Allowed in the Generic Backend

If a tenant eventually needs:

custom statuses

custom workflows

custom fields

They get:

Their own backend

Their own database

Their own deployment

Instruction:

The generic backend must stay generic.
All “special cases” belong in a dedicated backend.

9. Errors Must Be Standardized

You want:

{
  "error": "Invalid lead status",
  "details": { ... }
}


Not a dozen shapes.

Instruction:

Backend errors must always follow one structure so all frontends can handle them.

10. Document All Schemas in a “Contracts” Folder

This saves AI agents (and you) tons of time.

Add:

/contracts  
  lead.json  
  client.json  
  project.json  
  user.json  
  shared_types.json  


These documents define:

expected fields

enums

validation rules

required/optional

Instruction:

Keep a /contracts folder with all API schema definitions as the canonical handshake between front and back.


PathSix CRM – AI Implementation Map
0. Purpose of This Document

This document exists so any AI assistant or agent can:

Understand the architecture and constraints of PathSix CRM

Make incremental, safe changes without breaking existing behavior

Follow the same roadmap and conventions over time

Respect multi-tenant, white-label, and business rules

If an AI is modifying code, it should:

Read this doc.

Confirm its changes are aligned with the phases and rules here.

Prefer refactors and small improvements over rewrites unless explicitly asked.

1. High-Level Architecture

Frontend

Vite + React + TypeScript

Styling: Tailwind CSS (Tailwind CSS Plus) and Catalyst UI as preferred component sources

Shadcn UI / Radix UI are allowed only where Catalyst doesn’t cover a need

React Router for routing

Some views support offline mode + sync (important: don’t break this)

Backend

Python + Quart (async Flask-like framework)

JWT authentication (email-based login) using Authlib

SQLAlchemy ORM (migrating away from Flask-specific patterns where needed)

Postgres in production, SQLite in local dev (may be evolving)

Cross-Cutting

Multi-tenant: single backend, multiple frontends (per customer) sharing the instance

Tenant field is tenant_id (do not repurpose client_id for this)

Entities: Users, Tenants, Clients, Leads, Projects, Accounts, Contacts, Interactions, Notes, etc.

Soft delete with a Trash system for Clients, Leads, and Projects (see TrashPage.tsx)

2. Tenancy & Visibility Rules

Tenant model

Every business customer (who uses this CRM) is a tenant.

Each core record (Client, Lead, Project, Account, etc.) includes tenant_id.

Queries must always be filtered by tenant_id unless it is explicitly a cross-tenant admin tool (rare).

Visibility rules (per current design)

Users see only:

Leads they created or that are assigned to them

Clients they created or that are assigned to them

Admins:

On normal Leads/Clients pages: same rule as users (only their own/assigned)

Separate admin reports (like AdminLeadsPage) can see “all for the tenant” with extra filters.

When adding new routes or pages, an AI must:

Enforce tenant_id filtering.

Apply visibility rules consistent with the above.

Only bypass these rules in explicit admin/report endpoints.

3. Status & Validation Strategy
3.1 Standardized Status Fields (Core Backend)

Use generic, reusable statuses that are already in place. Do not change these.


These should be enforced via:

Pydantic models (request validation)

Zod schemas (frontend validation)

If a client wants wild statuses like "oil and gas", "food and beverage", "culverts", etc., that belongs in a custom backend or client-specific mapping layer, not the shared generic one.

3.2 Validation Plan

Frontend: Zod schemas per entity (Lead, Client, Project, etc.)

Backend: Pydantic models per route input/output

Both sides should validate:

Required fields

Enum values (like statuses)

Email format, phone formats, etc.

An AI modifying forms or APIs must:

Keep Zod and Pydantic in sync.

Never silently widen accepted values for shared status fields.

4. Auth, Security, and Roles

Auth: JWT-based, login by email, not username.

Roles:

At least admin and user roles (may expand later).

Routes:

Protected by @requires_auth (or equivalent) and role checks where needed.

Future: user deactivation (is_active) instead of hard deletion.

When adding new endpoints or pages:

Require auth unless it’s a login/health/public endpoint.

Apply role checks:

Admin-only pages: reports, user management, some global tools.

Normal users: limited to their own/assigned records.

5. Frontend Practices & UI Libraries
5.1 Primary UI Stack

React + TypeScript

Tailwind + Tailwind CSS Plus and Catalyst UI are the default building blocks.

Use Catalyst components first when building:

Forms

Tables

Modals

Layout elements

5.2 When to Use Shadcn / Radix

If Catalyst doesn’t provide:

An accessible nuanced component (e.g., complex menus, comboboxes, command palette)

Or you need low-level primitives for a custom interaction

Then:

Prefer Shadcn UI components built on Radix UI.

Keep styling consistent with Tailwind tokens in the rest of the app.

5.3 Example Existing Patterns

TrashPage.tsx:

Uses shared layout for Clients, Leads, Projects in one page

Supports:

Bulk selection and bulk purge

Restore/permanent delete per row

Responsive layout (cards on mobile, tables on desktop)

AdminLeadsPage.tsx:

Shows admin leads overview filtered by user email

Uses pagination, sorting, bulk delete, and edit modal

Pattern: dedicated admin “report-like” pages

When adding new list/report pages, copy these patterns instead of inventing new ones.

6. Backend Patterns
6.1 General Principles

Use async Quart routes.

Use SQLAlchemy ORM with sessions; avoid Flask-specific extensions.

Prefer explicit joins and joinedload for performance-sensitive list views.

Use soft-delete where applicable (e.g., Clients, Leads, Projects):

deleted_at timestamp

“Trash” view and restore/purge endpoints

6.2 Request Handling

Each route should generally:

Authenticate and authorize the user.

Determine tenant_id from the user/session.

Use a Pydantic model to parse/validate input.

Perform the DB operation within a clear session context.

Return JSON with consistent shapes and field names.

6.3 Error Handling

Always return structured error responses:

{ "error": "message", "details": {...optional...} }

Avoid leaking internal stack traces to the frontend.

Log full errors server-side for debugging.

7. Monitoring, Logging, and Backups

This is Step 1.5 in your plan and should be in place before heavy refactors.

7.1 Monitoring

Add error tracking (e.g., Sentry) to:

Backend (Quart)

Frontend (React)

Add request timing and slow-query logs.

7.2 Logging

Standardize logging:

Include tenant, user, route, and request ID where possible.

Don’t log sensitive information (passwords, full tokens).

7.3 Backups

For Postgres:

Daily automated backups.

Restore procedure documented (even if manual).

Before major schema changes or migrations, snapshot DB.

8. Performance & Database
8.1 Indexing Strategy

Add indexes on:

tenant_id

assigned_to, created_by

Status fields used in filtering (lead_status, etc.)

deleted_at for Trash views

Any field frequently used in WHERE or ORDER BY

8.2 N+1 Query Avoidance

Use joinedload / selectinload on relationships frequently shown in lists:

e.g., leads with assigned user, clients with main contact, etc.

Guidelines for relationship loading:
- joinedload: Use for many-to-one relationships (Lead -> User)
- selectinload: Use for one-to-many relationships (Client -> Projects)
- Avoid loading relationships not displayed in the current view

Keep list endpoints lean:

Only include fields needed for the view

Heavy data should be fetched via detail endpoints

8.3 Performance Thresholds

Database queries:
- List views: < 200ms response time
- Detail views: < 100ms response time  
- Search operations: < 500ms response time
- Bulk operations: < 2s per 100 records

API endpoints:
- 95th percentile response time < 500ms
- Error rate < 1% under normal load
- Support 100+ concurrent tenant users

8.4 Database Connection Management

Connection pooling:
- Pool size: 10-20 connections for production
- Max overflow: 5 additional connections
- Connection timeout: 30 seconds
- Pool recycle time: 3600 seconds (1 hour)

8.5 Pagination Standards

For large datasets:
- Default page size: 25 items
- Maximum page size: 100 items
- Use cursor-based pagination for real-time data
- Include total count only when specifically requested (performance impact)

9. Features & Roadmap Phases (for AIs to respect)
Phase 1 – Validation Standardization

Implement Zod & Pydantic schemas for:

Leads

Clients

Projects

Accounts

Replace ad-hoc status handling with generic options:

PROJECT_STATUS_OPTIONS, CLIENT_STATUS_OPTIONS, LEAD_STATUS_OPTIONS

Phase 1.5 – Monitoring & Visibility

Integrate error tracking & performance monitoring.

Add basic query logging.

Ensure tenant_id and visibility rules are correctly enforced everywhere.

Phase 2 – Code Cleanup & Stability

Remove dead code and unused endpoints.

Fix N+1 queries and add indexes.

Normalize error responses.

Audit usages of @requires_auth and role checks.

Phase 3 – Reports

Expand admin-style overview pages (like AdminLeadsPage) for:

Clients

Projects

Accounts

Provide:

Filters (date, status, user)

Pagination

Export options (CSV/Excel in future)

Make them read-only in the first pass, then add safe admin actions (bulk assign, bulk status change, etc.) later.

Phase 4 – Custom Backends

Keep the default backend generic.

For high-paying/custom clients:

Introduce client-specific status pipelines, workflows, and business rules in separate configuration or dedicated deployments.

The shared codebase must not be polluted with one-off logic that only applies to a single client.

Phase 4.5 – Schema Migration Strategy

Multi-tenant migration approach:
- Maintain API versioning (/api/v1/, /api/v2/)
- Backward compatibility for at least 2 major versions
- Tenant-specific feature flags for gradual rollouts
- Frontend version detection and compatibility warnings

Migration coordination:
- Blue/green deployments for zero-downtime updates
- Database migrations run automatically with deployment
- Feature toggles for new functionality during transition
- Tenant notification system for breaking changes (30+ days notice)

10. Redis & Rate Limiting

Redis should be introduced where appropriate for:

Rate limiting:

Login endpoint

Search endpoints

Bulk operations

Optional caching for:

Heavy, read-mostly report queries

Reference data (status lists, user lists)

Any AI adding rate limiting should:

Use Redis as the store (not in-memory).

Keep configuration simple and per-tenant if relevant.

11. Offline & Sync (Important Not to Break)

Some parts of the app are offline-capable:

Local storage or IndexedDB is used to store data.

Sync logic pushes changes back to the backend when online.

Token validity and sync timing have already been tuned; don't rework auth or sync behavior without explicit instruction.

If an AI touches offline-related components:

Reuse existing patterns in ClientDetailPage, Projects, and LeadDetailPage (where offline refactors have been done).

Only adjust behavior where clearly requested.

12. Testing Strategy

12.1 Multi-Tenant Testing Patterns

Tenant isolation tests:
- Verify tenant_id filtering in all queries
- Test cross-tenant data leakage scenarios
- Validate user visibility rules per tenant
- Test bulk operations respect tenant boundaries

Integration testing:
- Frontend form validation matches backend Pydantic schemas
- Zod and Pydantic schema synchronization tests
- API contract testing (request/response shapes)
- Database constraint enforcement

12.2 Test Categories

Unit tests:
- Business logic functions
- Validation schemas (Pydantic models)
- Utility functions (phone formatting, email validation)
- Database model relationships

Integration tests:
- Complete API endpoint flows
- Authentication and authorization
- Database transactions and rollbacks
- File upload/storage operations

End-to-end tests:
- Critical user journeys per tenant
- Offline sync functionality
- Multi-user collaboration scenarios
- Admin operations (bulk actions, user management)

12.3 Testing Environment Setup

Test data isolation:
- Each test uses separate tenant_id
- Clean database state between test suites
- Seed data that represents realistic tenant scenarios
- Mock external services (email, storage)

Performance testing:
- Load testing with multiple simulated tenants
- Database query performance under load
- Memory usage patterns with tenant data
- Rate limiting effectiveness

12.4 Continuous Testing Requirements

Pre-commit hooks:
- Run linting and type checking
- Execute fast unit test suite
- Validate schema consistency

CI/CD pipeline:
- Full test suite on every pull request
- Integration tests against real database
- Performance regression detection
- Security vulnerability scanning

13. Coding Rules for Any AI Working on This Project

When modifying this repo, any AI should follow these rules:

Small, incremental changes.

Prefer focused edits over broad rewrites.

Respect existing patterns.

For lists + bulk actions, copy patterns in TrashPage and AdminLeadsPage.

Never remove tenant filtering.

Always respect tenant_id and visibility rules.

Prefer Catalyst + Tailwind components.

Only reach for Shadcn/Radix when necessary.

Keep auth flow intact.

Don’t re-architect login, tokens, or roles unless explicitly requested.

Keep validation consistent.

Whenever you update a schema, update both Zod (frontend) and Pydantic (backend).

No magic renames of core fields.

tenant_id, lead_status, client_status, etc., should not be renamed without a clear migration plan.

Don’t break offline features.

Assume offline behavior is important unless told otherwise.

Add comments where behavior is non-obvious.

Especially around visibility rules, custom business logic, or edge cases.