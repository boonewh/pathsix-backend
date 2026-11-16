# Production go-live checklist (November 16, 2026, 7:00 PM Central)

Releases the staging backend and web app together, moves production onto the
restricted `pathsix_crm_runtime` login with tenant row security, and opens the
AI connector at `https://api.pathsix.app/mcp`. Rehearsed on a restored copy the
same day (see `mcp-readiness-status.md`, "Phase 7: production rehearsal").
Expected downtime: about 15 minutes. The owner approves each step.

Names used below: backend app `pathsixsolutions-backend` (machine
`d894111b636938`), database app `pathsixsolutions-db` (volume
`vol_vx2jzeg776zonzjr`), release image `registry.fly.io/pathsixsolutions-backend:release-<sha>`.
Connection values never appear on screen: they are read inside a Fly machine
or piped between commands.

## Before 7:00 PM

- [ ] `api.pathsix.app` CNAME in Gandi; `flyctl certs check api.pathsix.app --app pathsixsolutions-backend` shows issued.
- [ ] Production Resend key staged: `flyctl secrets set --stage RESEND_API_KEY=... --app pathsixsolutions-backend`.
      `MAIL_FROM_EMAIL=no-reply@pathsix.app` and `MAIL_REPLY_TO` are already staged.
- [ ] Owner decision on the 6 rule-breaking records (projects 5, 6, 330; interactions 829, 948, 1092).
- [ ] Backend and frontend release pull requests (`staging` -> `main`) open with CI passing.
- [ ] Release image built and pushed without deploying:
      `flyctl deploy --build-only --push --image-label release-<sha> --config fly.toml --app pathsixsolutions-backend`.
- [ ] `crm.pathsix.app` added to Vercel's **production** project (`pathsix-crm`) and its CNAME in Gandi.
      `CORS_ALLOWED_ORIGINS=https://crm.pathsix.app` is already staged on the backend.
- [ ] Downtime email sent.

## At 7:00 PM

1. **Snapshot:** `flyctl volumes snapshots create vol_vx2jzeg776zonzjr --app pathsixsolutions-db`; confirm it is listed.
2. **Stop the old version** and keep it from waking up:
   `flyctl machine stop d894111b636938 --app pathsixsolutions-backend`, then
   `flyctl machine update d894111b636938 --app pathsixsolutions-backend --autostart=false --skip-start --yes`.
3. **Temporary operations machine** (new image, no web services, admin login in its env):
   `flyctl machine run <release image> sleep 3600 --app pathsixsolutions-backend --region ord --name golive-ops --env CRM_RLS_ENABLED=1 --vm-memory 512 --restart no`.
   Every step below runs on it: `flyctl ssh console --app pathsixsolutions-backend --machine <ops id> -C "sh -c '...'"`.
4. **Repair the reviewed records** (dry run, then `--apply`):
   `printenv DATABASE_URL | python /app/scripts/repair_parent_links.py --target production --expect-projects 5,6,330 --expect-interactions 829,948,1092`.
5. **Restricted login:** `printenv DATABASE_URL | python /app/scripts/staging_database_role.py --target production`.
6. **The 14 migrations**, each as a trial then `--apply`, stopping at the first failure:
   `printenv DATABASE_URL | python /app/scripts/migrate_staging_membership.py --target production --revision <rev> [--apply]`
   in order: tenant_membership_indexes, tenant_relationships, tenant_rls_prepare, tenant_row_security,
   parent_link_rules, ai_consent_grants, oauth_browser_flow, mcp_read_audit, mcp_lead_creation,
   mcp_client_registration, ai_connection_idle_expiry, mcp_published_scopes_v2, mcp_record_creation,
   mcp_record_updates.
7. **Trust ChatGPT** (preview, then `--apply`):
   `printenv DATABASE_URL | python /app/scripts/trust_ai_client.py --client chatgpt`.
8. **Switch logins without showing them:**
   - runtime login into the app's settings: read `runtime_url` from
     `/tmp/pathsix-production-runtime-credentials.json` on the ops machine as
     `DATABASE_URL=...` and pipe it to `flyctl secrets import --stage --app pathsixsolutions-backend`;
   - admin login to the operator workstation: pipe `previous_url` to
     `G:\Projects\tools\pathsix-prod-admin-store.ps1` (DPAPI). It is no longer in the app's settings.
9. **Deploy:** `flyctl deploy --config fly.toml --app pathsixsolutions-backend --image <release image>`
   (restores automatic start from `fly.toml`).
10. **Verify:** health on both addresses; on the new app machine,
    `<admin-url helper> | python /app/scripts/verify_tenant_access.py --target production` reports no problems;
    the owner logs in on the web.
11. **Remove the ops machine:** delete the handoff file, then `flyctl machine destroy <ops id> --force`.
12. **Web app:** in Vercel's production project set `VITE_API_BASE_URL=https://api.pathsix.app/api`,
    then merge the frontend pull request; check the live site logs in and loads records at both
    `crm.pathsix.app` and `pathsix-crm.vercel.app`. Then point reset links at the new address:
    `flyctl secrets set FRONTEND_URL=https://crm.pathsix.app --app pathsixsolutions-backend`.
13. **After:** move the Sentry uptime monitor to `https://api.pathsix.app/api/health`; unset the old
    `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_USE_TLS` secrets; announce.

## If something goes wrong

- **Before step 6 finishes:** each migration is one transaction, so a failure leaves the
  previous state. Restore the old version: `flyctl machine update d894111b636938 --autostart=true --yes`
  and start it. The old code connects as the admin login, which row security does not restrict.
- **After the deploy:** redeploy the previous image `pathsixsolutions-backend:prod-02bd452` with the
  admin `DATABASE_URL` restored from the workstation, or restore the step-1 snapshot into a new
  database (runbook section 6). Never overwrite the live database.
- **AI connector only:** switch it off with `flyctl secrets set MCP_RESOURCE_URI=off` (runbook section 4).
