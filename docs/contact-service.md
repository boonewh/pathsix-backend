# Tenant-bound contact service

ContactService owns list, create, update and delete for the contact API. It requires
an authenticated Principal and binds the transaction-local database context. Every
contact lookup is tenant-scoped; access is inherited from the active client or lead
through the same admin/creator/assignee predicate used by the other shared services.

Updates authorize the existing parent before authorizing the final parent state.
Moving a contact requires access to both parents and setting both fields atomically
when changing parent type. Exactly one parent is required. Lists explicitly exclude
legacy malformed dual-parent rows. An unfiltered list retains its empty response.
Field serialization and phone/email normalization are preserved. Non-object JSON
and explicit null first names return 400. Services flush but do not commit, and list
reads do not create activity events; HTTP adapters own transaction commits.

## Validation

Local suite: 96 passed, 29 PostgreSQL-only skipped, with fail-fast enabled. New
direct-service tests cover foreign/current/destination parent authorization,
ordinary-user denial, deleted parents, foreign contacts, pure reads, atomic transfer
and rollback. Existing HTTP parent-access and relationship tests also passed.

Staging v24 / 6681740 verified: 17 focused PostgreSQL checks passed in 15.47 seconds
with RLS, safe UTC diagnostics and fail-fast enabled. The selection covered contact
tests, database parent combinations, client/lead purge conflicts and harness cleanup.
This was a focused run, not the full PostgreSQL suite. Window: September 9
00:55:10–00:55:26 UTC. Connections were 14 before/after, schemas zero before/after,
and PostgreSQL uptime unchanged. The concurrent filtered Fly stream reported no new
matching connection errors; the older intermittent failure remains unresolved.

Live browser login, protected reads, contact create/list, rejected dual-parent
update, atomic client-to-lead transfer and delete passed without browser errors.
Synthetic parent records were removed. Independent inspection confirmed original
two clients/two leads, empty contacts/interactions/projects, fourteen forced RLS
tables, parent_link_rules head and zero unscoped runtime reads.

The first image build stopped on a protected local pytest output directory before
deployment. .pytest-harness*/ is now excluded from Docker and Git; files were
preserved. Retry deployed successfully. No frontend/production deployment or Fly
resource size/count changes occurred. Production is not authorized for deployment.
