# PathSix CRM agent instructions

## Branches and deployments

- `main` is the production CRM (Fly app `pathsixsolutions-backend`).
- `staging` is the AI connector MVP and the working branch (Fly app
  `pathsixsolutions-backend-staging`). The former `codex/mvp-lead-creation`
  branch was merged into it (PR 27) and is retired. Never merge or deploy MVP work to
  production without the user's explicit decision.
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

## Active MVP scope lock

Read `docs/mcp-readiness-status.md` before planning or changing this project. Its
locked capabilities, implementation criteria and completion checklist define the
only planned build until the MVP is finished. Historical roadmaps do not expand it.

- Implement the MVP and necessary supporting changes; do not add speculative
  improvements, scaling work, extra tools or broad refactors along the way.
- You may investigate and fix discovered bugs or issues without separate scope
  approval. Keep repairs bounded to intended behavior, security, data integrity
  or reliability, and document relevant verification. Do not label an enhancement
  a bug fix to bypass the scope lock.
- If you believe something outside the MVP should be done, explicitly identify it
  as outside scope, explain the reason and impact, and ask the user before doing
  it. Wait for their decision on that work while continuing independent MVP work.
- Record other issues or ideas in the plan's final `Future consideration` section.
  That list is not authorized work. Only the user can approve scope expansion.
- Once the MVP is complete, report its evidence and external release status; do
  not automatically start deferred work. Preserve existing operational permission
  requirements and distinguish source, test and deployment status.
