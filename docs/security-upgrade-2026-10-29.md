# Security dependency/runtime upgrade — October 29, 2026

Production baseline: main531f603, separate from the unfinished MCP MVP on staging.
No database migrations, model changes, signing-key changes, new features or
customer-record edits are part of this maintenance release.

## Runtime and dependency policy

Use Python3.12.16 and uv0.13.6+ locally. `requirements.in` records the reviewed
constraints; `requirements.txt` is the complete universal hashed runtime lock.
`requirements-test.in` adds pytest9.1.1 and generates `requirements-test.txt`.
Install with `uv pip sync --require-hashes requirements-test.txt` in an isolated
environment. Regenerate with `uv pip compile --python-version 3.12 --universal
--generate-hashes --output-file requirements.txt requirements.in` (and similarly
for tests); review changes rather than blindly upgrading every major version.

Quart0.22.0 is the latest Python3.12-compatible release (Quart0.23 requires3.13).
Hypercorn0.18.0, Flask3.1.3, Werkzeug3.1.9, Authlib1.9.1, cryptography50.0.2,
aiosmtplib5.1.3 and other security fixes are locked. Supporting cffi2.0.0,
typing_extensions4.16.0, packaging26.3, platformdirs4.14.0, distlib0.4.4 and
limits5.8.0 satisfy the security-fixed tools' dependency constraints.
SQLAlchemy stays on2.0 (2.0.51); Pydantic2.13.5/psycopg2 2.9.13 are compatible
updates. Redis/RQ, pandas and bcrypt major migrations are outside this security
fix; existing password hashing/queues/import business contracts are retained.

Docker pins Python3.12.16 and its reviewed image digest. It installs patched
native CA/Brotli packages, pip26.2.2 and hashed dependencies. The offline
`scripts/verify_security_runtime.py` fails builds with the compromised emSign root
in app/pip/system CA bundles, unexpected Brotli/brotlicffi/LiteLLM distributions,
an unreviewed Python/pip/certifi version, or stale Debian CA/Brotli/OpenSSL packages.
It imports no application and connects to no database. Debian's OpenSSL package
revision, rather than its upstream version string alone, establishes backports.

certifi2026.10.23 supplies default urllib trust (explicit SSL_CERT_FILE operator
overrides remain honored). Mail explicitly verifies certificates/hostnames with
that bundle and a15-second timeout. Current SMTP host/credentials/envelopes remain.
Reset links/tokens and mail-recipient/error text are no longer printed by the
forgot-password handler; failure is logged by exception type only.

Authlib's legacy jose API emits a deprecation warning but remains supported
before2.0. Existing HS256 integer/string subject tokens, expiry/signature checks,
password hashes and reset tokens are covered. A future joserfc migration requires
its own compatibility review; do not change token/signing semantics casually.
Existing datetime.utcnow/SQLAlchemy advisory warnings are tracked maintenance,
not failing upgrade checks. Container UID/permissions and broader auth/rate-limit
architecture remain existing behavior; do not change storage ownership blindly.

## Verification and release boundaries

Run `SECRET_KEY=test-only DATABASE_URL=sqlite:///test-unused.db SENTRY_DSN= python -m pytest -q`
with disposable fixtures. PostgreSQL tests use only a dedicated
PURGE_TEST_DATABASE_URL; fixtures create/drop random test schemas there. Never
point these destructive fixture operations at a shared database. The existing
CI uses its disposable PostgreSQL18 service, then builds/guards/import-checks the
exact production Dockerfile. Actions are pinned; one combined job limits runner
overhead. A production image import does not start Quart's database warmup hook.

Production Fly resources/region/always-on settings and secrets remain unchanged.
No release_command runs migrations. Check health, unauthenticated denials, CORS,
source fingerprints and the installed runtime after deployment; avoid real
record edits or password-reset messages as smoke tests. Record the previous
image for rollback, noting that it contains old vulnerable dependencies.

The original outer and integrated MVP checkouts contain unpublished work and
remain untouched. The MVP uses additional dependencies and a different schema/
application lineage: prepare security updates separately, test its OAuth/MCP/RLS
suite and never replace staging with production main or promote the unfinished
MVP by accident. Dependency maintenance does not close its open acceptance gates.
