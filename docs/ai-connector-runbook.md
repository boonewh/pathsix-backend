# AI connector runbook

Short operating guide for the PathSix AI connector (MCP + OAuth). Commands use
the staging app; production is `pathsixsolutions-backend` with database app
`pathsixsolutions-db`. Production changes need the owner's explicit go-ahead.

| | Staging | Production |
| --- | --- | --- |
| Backend app | `pathsixsolutions-backend-staging` | `pathsixsolutions-backend` |
| Database app | `pathsixsolutions-db-staging` | `pathsixsolutions-db` |
| Connector URL | `https://api-staging.pathsix.app/mcp` | `https://api.pathsix.app/mcp` (after rollout) |

## 1. See what is happening

- Logs: `flyctl logs --app <app> --no-tail`. Each MCP request logs two lines,
  `MCP request started id=…` and `finished id=… status=… phase=…`, with no
  tokens, arguments or record contents.
- Status meanings: `401` expired/revoked connection (the AI app reconnects);
  `421` the AI app uses an old address (remove and re-add the connector);
  `429` a rate limit (below); `503` the connector is switched off or the
  database is unavailable.
- Errors: Sentry project `python-quart` (production only). Reports carry the
  error type and code location only; see `app/utils/sentry_privacy.py`.
- Health: `curl https://<backend-host>/api/health` returns `{"status":"ok"}`.

## 2. Rate limits

| Entry point | Limit | Counted per |
| --- | --- | --- |
| `/mcp` failed sign-ins (bad tokens) | 30 / minute | visitor address, before any database work |
| `/mcp` signed-in requests | 120 / minute | AI connection |
| OAuth sign-in, token, registration pages | 10–60 / minute | visitor address |
| Proposals | daily cap | connection (database) |
| Token exchanges | 20 recent | connection (database) |
| Self-registered AI clients | 200 / hour | whole service (database) |

The per-address and per-connection limits are kept in each machine's memory and
reset on restart; with two machines a visitor can get up to twice the number.
The database limits are shared. The visitor address comes from Fly's
`Fly-Client-IP` header; other forwarded headers are ignored.

## 3. Revoke access

- **One person's connection:** they open `/oauth/connections` (Settings → AI
  connections → Manage) and revoke it. Takes effect on the next request.
- **Operator, one connection** (admin database login, see section 6):
  `UPDATE ai_connections SET revoked_at = now() AT TIME ZONE 'UTC' WHERE id = '<id>' AND revoked_at IS NULL;`
- **Every connection of one AI app** (for example a compromised client):
  `UPDATE ai_clients SET is_active = false WHERE id = '<client id>';`
  New sign-ins and existing tokens for that client stop working.

## 4. Switch the connector off (kill switch)

The connector refuses all traffic with `503` when `MCP_RESOURCE_URI` is not a
valid https URL. The CRM web app is unaffected.

    flyctl secrets set MCP_RESOURCE_URI=off --app <app>      # off
    flyctl secrets unset MCP_RESOURCE_URI --app <app>        # back on (fly.toml value)

Existing connections resume when it is back on; nobody needs to reconnect.

## 5. Roll back a release

1. `flyctl releases --app <app> --image` lists releases with their images.
2. Redeploy the last good image:
   `flyctl deploy --app <app> --config <fly config> --image <registry image>`.
3. Database migrations are forward-only. Roll back code only to a release that
   works with the current schema (every release since `mcp_record_updates`
   does). Never downgrade a migration on production without a reviewed plan.

## 6. Database backups and restore

- Fly snapshots each database volume daily and keeps 14:
  `flyctl volumes list --app <db app>`, then
  `flyctl volumes snapshots list <volume id> --app <db app>`.
- Restore into a **new** database, never over the live one:
  `flyctl postgres create --snapshot-id <snapshot id> --name <new name>`, check
  the data, then point the backend's `DATABASE_URL` at it in a reviewed step.
- Before every production migration, take a fresh snapshot:
  `flyctl volumes snapshots create <volume id> --app <db app>`.
- The admin database login is kept DPAPI-protected on the operator workstation
  and only ever piped to commands, never printed or passed as an argument.

## 7. Retention (monthly)

`scripts/prune_ai_records.py` deletes connections that ended more than 90 days
ago (with their credentials, audits and receipts) and credentials that expired
more than 30 days ago. CRM records are never touched. It refuses an admin login
for a different database than the app's own. Dry run first, then apply:

    <admin-url helper> | flyctl ssh console --app <app> -C "python scripts/prune_ai_records.py"
    <admin-url helper> | flyctl ssh console --app <app> -C "python scripts/prune_ai_records.py --apply"

## 8. Incident checklist

1. Is it the connector or the whole CRM? Check health and the web login.
2. If data exposure is possible: switch the connector off (section 4), then
   revoke the affected connections or client (section 3).
3. Find the cause in logs and Sentry; fix, test, deploy to staging, then production.
4. Switch back on and confirm a read from a test account.
5. Write down what happened, when, who was affected and what changed.
