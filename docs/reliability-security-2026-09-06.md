# CRM reliability and security reconciliation — 2026-09-06

Update: staging now runs v9 (`c88ce35`), with 61 passing tests. See
[the service foundation report](tenant-service-foundation.md) for the next milestone.
The v8 results below describe the earlier hardening milestone.

## Preserved baseline

The frontend handoff dated September 3 is historical. Backend work started on
`codex/admin-password-reset` at `6bd505e`, following `31d65fe` (admin reset emails)
and `dcecadc` (configuration hardening). All four pre-existing uncommitted files
were preserved unchanged in commit `4401b02` on `codex/crm-reliability-security`.
Frontend HEAD is `540641b`, following `a000b9a` and all six recovery/security commits.
Its untracked `.codex-remote-attachments/` directory was left intact.
Resend delivery was reported working by the user; its configuration is preserved.
Reset links are no longer printed by the current email helper.

## Fixes and policy

- Independent SQLAlchemy sessions; one retry for a disconnected GET/HEAD, only
  before any ORM write/commit has begun. Startup closes failed warmup sessions.
- JWT signature algorithm pinned to HS256; required expiry and subject validated.
  Current database roles and active user/tenant status govern every protected route.
- Whole-database backup blueprint removed from the tenant HTTP app entirely.
- Reports and Lead Import require a current tenant admin role.
- Calendar exports require login, tenant ownership and parent record access.
- Client source leads, account clients, contact/project parents, interaction
  create/update parents and both transfer endpoints' IDs are validated before writes.
- Contacts and accounts inherit their parent record permissions. Clients/leads
  permit their creator or assigned user; tenant admins can access their own tenant.
  Projects honor direct assignment first, otherwise inherit their linked parent,
  otherwise their creator. Standalone projects remain supported. Contacts and
  interactions require exactly one parent; projects permit zero or one.
- Client restore and project detail/update/delete enforce record access.
- Authenticated ORM queries also scope relationship loads, joins and aggregates to
  the current tenant, protecting against legacy malformed cross-tenant relationships.
  This does not replace explicit predicates, background-job scoping or database RLS.
- Authentication rate limiting ignores arbitrary forwarded headers. Until trusted
  ingress is explicitly configured, clients behind a proxy can share a limit bucket.
  Limits remain per process; distributed abuse protection remains future work.
- Reset submission is rate limited. Existing SMTP error reporting and reset limits
  are preserved. Sentry request bodies/default PII are disabled, tracing is sampled.
- Docker build context excludes local databases, environment files, venv and Git.
- CI installs dependencies and runs pytest, with PostgreSQL-backed isolation tests.
  The old regex audit command now runs behavioral boundary tests.

## Verification and live observations

Local backend: 56 tests passed; compile check and diff whitespace check passed.
Frontend: typecheck/build and six Playwright regressions passed (September 6).
Nonblocking frontend warnings: old Browserslist data and large bundle.

Read-only Fly inspection: production v80, September 4, one running healthy machine
in ord. This is newer than the handoff. Release metadata alone does not prove the
exact source SHA. No production settings, machines or deployment were changed.
Before deployment, staging v6 from August 1 had two sleeping app machines in iad.
Staging database is running with three passing checks; hostname is
`pathsixsolutions-db-staging.flycast`. Public schema has tenant slug `staging`,
only test-domain users, two clients and two leads. This matches the synthetic seeder.
Staging secrets contain DATABASE_URL, SECRET_KEY and CORS_ALLOWED_ORIGINS only;
no SMTP secrets are configured there. Production Resend configuration was not copied.

Final staging backend: release **v8**, image/revision
`186e6c94461083be1a6642293a332145c237f6dd` (application fixes `7c4fd50`).
Both Fly machines passed deployment health checks. The first PostgreSQL run found
fixture IDs did not advance PostgreSQL sequences; `186e6c9` fixes the fixture,
without changing application behavior. The corrected full suite passed **56 tests**
against PostgreSQL in **24.12s**. Local suite also passed 56 tests after correction.
Post-test verification found **zero** remaining `security_test_*` schemas and
unchanged demo counts: one tenant, two clients, two leads, only test-domain users.

Frontend staging: `cdab5ed4c92fac04e0e01d0404e3de57e9c1aa0d`, merged from the
locally tested recovery branch. Its application tree exactly matches `540641b`.
Vercel succeeded for the staging project; the normal CRM project created only a
preview. Neither production main nor the real production CRM deployment changed.
Staging URL: https://pathsixdesigns-crm-staging.vercel.app
Immutable deployment: https://pathsixdesigns-crm-staging-9o3csfb49-boonewhs-projects.vercel.app
The separate staging checkout is `G:\Projects\pathsixdesigns-crm-staging`, branch
`codex/crm-staging-validation`, pushed only to remote `staging`.

Live browser smoke: synthetic admin login and dashboard passed with zero page errors;
all observed API traffic targeted only `pathsixsolutions-backend-staging.fly.dev`.
22 authenticated read endpoints passed (me, clients, leads, interactions, recent
activity, search, accounts, projects and all GET reports). Public calendar returned
401 and removed backup API returned 404. Staging SMTP delivery remains unverified
because staging has no mail secrets; SMTP behavior is covered by mocked tests.

Re-run staging suite using these commands (the script checks app and database host):

```text
flyctl ssh console --app pathsixsolutions-backend-staging --machine 82549ec7079218 -C '/venv/bin/pip install --target /tmp/crm-test-deps -r /app/requirements-test.txt packaging==24.2'
flyctl ssh console --app pathsixsolutions-backend-staging --machine 82549ec7079218 -C 'env PYTHONPATH=/tmp/crm-test-deps /venv/bin/python /app/scripts/test_staging_security.py'
```

The runner uses disposable schemas and mocks SMTP. It does not seed or restore the
public schema. PostgreSQL tests are also configured in CI; no remote backend CI
run or branch-protection setting change was made during this session.

## Remaining roadmap boundaries

This work closes the known immediate REST gaps, not all MCP gates. No MCP endpoint
is exposed. Database constraints/RLS, service extraction, OAuth delegation, durable
MCP audit events, distributed limits, and external security review remain separate.
Full six-app volume/snapshot/cost inventory and backup restore drills remain open.
No legacy resource was removed; no production deployment is authorized by this work.
