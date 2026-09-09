# Production sales activity report — September 9, 2026

## Released

Backend application commit c71a781 and frontend commit 269bdd3 were pushed to
their main branches. Backend release 81 deployed successfully to the existing
d894111b636938 machine; its health check passes. The public frontend at
https://pathsix-crm.vercel.app/reports serves the new report bundle. Vercel production
deployment 6356054456 and frontend typecheck/build CI succeeded. The staging
production deployment remains on cdab5ed; Vercel also built its normal preview.

Live authenticated HTTP checks passed for a seven-day range, a single-day range
and page two. The first two returned different totals. Unauthenticated access
returned 401. These checks performed no CRM mutations or email sends. Eight new
backend tests and three Playwright regressions pass. TypeScript and targeted lint
pass; the production build passes. The browser screenshot was inspected. No
database migration, secret changes or Fly resource resizing was required.

Keep this main hotfix when later promoting the separate security/service work.
Historical data limitations below remain intentional. Preparation notes follow.

Based on origin/main dcecadc, isolated from codex/crm-reliability-security.

GET /api/reports/sales-activity requires authentication and the user's current
admin role. Date parameters are strict YYYY-MM-DD in UTC with an inclusive end
day. Optional user_id and page filter the report; detail pages contain 50 rows.
All source queries are tenant scoped. Summary groups and pagination run in SQL.

The report credits recorded actors, never current assignees. It includes lead,
client, project, subscription and file creation records, historical latest edits
and soft deletions, and saved activity logs. Legacy interactions appear without
an author. Logged creation/deletion events suppress matching historical row
fallbacks. Inactive users remain in the report. Historical missing/purged records
and overwritten edits cannot be reconstructed.

Transactional mapper hooks record new creates, edits and deletes for leads,
clients, projects, interactions, contacts, accounts, subscriptions and files.
Bulk lead/client/project deletions are explicitly logged in the same transaction.
Soft deletion is classified as deleted; restoration and assignment changes are
edits. Private chat, authentication, settings, external work and direct SQL or
maintenance writes are not sales-record activity and are not captured here.

Verification: eight focused SQLite tests pass (actor attribution, inactive users,
tenants, end-day boundaries, invalid dates, deduplication, pagination, rollback,
soft deletion/restore and stale-role rejection). Main's two pre-existing lead
schema validation tests fail; the other five baseline tests pass. The new report
query was also exercised against production PostgreSQL in an enforced read-only
transaction, before the final equivalent historical-deletion fallback addition.
Production activitytype has viewed/created/edited/deleted, so no migration is
required. Fly prints a Windows handle error after returning successful SSH output;
do not mistake that client error for a server query failure.

No production writes or deployment were performed during preparation. A normalized
Python source hash comparison matched main except app/config.py, consistent with
the earlier undeployed configuration-hardening work. Before release, reconcile that
file and preserve live Fly machine settings and Resend secrets. The difference
was checked: production retains the older SECRET_KEY fallback and Sentry defaults;
SECRET_KEY and SENTRY_DSN are both already deployed secrets, so main's required
environment configuration is compatible. The production machine matches fly.toml:
one shared CPU, 1024 MB, always on, with the 15-second health gate. Deploy the backend
before the frontend. Do not promote the unrelated staging security branch.
