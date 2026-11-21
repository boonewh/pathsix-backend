# PathSix CRM agent instructions

## Branches and deployments

- `main` is the production CRM (Fly app `pathsixsolutions-backend`).
- `staging` is the working branch (Fly app `pathsixsolutions-backend-staging`).
  The former `codex/mvp-lead-creation` branch was merged into it (PR 27) and is
  retired. Never merge or deploy `staging` work to production without the
  user's explicit decision.
- Local PostgreSQL for the full test suite: `G:\Projects\tools\pathsix-test-db.sh
  start`, then `eval "$(bash G:/Projects/tools/pathsix-test-db.sh env)"`.

## Testing tiers (keep local runs small; CI runs everything)

Tests are tagged by file in `tests/conftest.py`. GitHub CI runs the full suite
on every push and is the single full check, so do not also run the full suite
locally.

- `pytest -m core`: always run. Covers the AI connector (MCP, OAuth,
  reviewed actions); about 300 tests.
- `pytest -m isolation`: when shared services, access rules, models or routes
  change.
- `pytest -m crm`: when web CRM routes or services change.
- `pytest -m migration`: when a migration changes, and before a production
  rollout.
- `pytest -m ops`: when the operational scripts they cover change.

While iterating, run only the test files for the code you changed, then the
`core` tier (plus any tier above that your change touches) before pushing. Let
CI confirm everything else.

## AI connector status: production beta (November 20, 2026)

The AI connector is a beta in production (`https://api.pathsix.app/mcp`). The
user lifted the MVP scope lock on November 20, 2026, so new web CRM work may
start on `staging`. `docs/mcp-readiness-status.md` records the connector's
history and its open beta follow-up items (manual walkthroughs, other-client
checks, retention and restore drills, the OpenAI submission package); read it
before changing the connector.

- Keep each change bounded to what the user asked for; ask before broad
  refactors or schema redesigns.
- The connector's tools are live in production: treat changes to their inputs
  or outputs as breaking for connected AI clients.
- Record ideas that are not yet approved in that document's
  `Future consideration` section. That list is not authorized work.
- Preserve existing operational permission requirements, and distinguish
  source, test and deployment status when reporting.
