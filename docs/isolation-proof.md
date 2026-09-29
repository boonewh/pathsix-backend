# Web isolation proof and drift detection — September 29, 2026

This increment closes the remaining session-identity and deliberate-failure proof
gaps in the current web isolation foundation. It does not enable delegated AI access.

## Identity fixes

A database session keeps one identity across commit, rollback and close/reuse.
Authentication lookup sessions cannot change users, gain password-reset authority or
be promoted into tenant sessions. Tenant sessions reject a different authenticated
request, an anonymous request and already-cached foreign tenant records. Validation
runs before cached `get`, ORM execution, transaction start, flush and commit. This
closes the cases where no new query or transaction would trigger the database checks.
Each normal request still creates fresh sessions.

Principal IDs must be positive integers; role names are copied into a frozen set.
Only the implemented web authentication boundary is accepted. A caller cannot label
an ordinary identity as MCP/worker authorization. Delegated connection/grant validation
must be implemented before another authentication boundary can be accepted.

## Evidence inventory

| Boundary | Executable evidence |
| --- | --- |
| Every current model classified | `test_isolation_proof.py` checks all 17 models: 14 RLS-protected tables, global role vocabulary, two platform-only backup tables. Adding a model fails this inventory until its classification is reviewed. |
| Session/cache/transaction reuse | New proof tests cover commit, rollback, close, implicit HTTP identity, cached rows, anonymous reuse and writes in an already-open transaction. |
| Application and record predicates | Fault tests independently remove the service tenant predicate and client ownership predicate using operator fixtures, so RLS cannot mask their failure. The original denial assertions must then fail. |
| Database row boundary | All 14 protected tables are exercised with restricted credentials, missing context and foreign identities. Available tables deny foreign reads/updates/deletes and identity reparenting. Tenants is read-only; disabled chat has no table privileges. |
| Policy drift | Read-only `ops.isolation_contract.verify_isolation_contract` checks role flags/elevated membership, ownership, ENABLE/FORCE RLS and the exact 20 reviewed policy names, commands, targets and expressions. |
| Deliberate policy failures | Four mutations on each of 14 tables (56 injections): missing policy, weaker predicate, extra permissive policy, disabled FORCE RLS. Each must fail attestation and pass again after rollback. A separate weakened-policy test proves actual foreign rows become visible to the unchanged row-denial assertion. |
| HTTP lifecycle and aggregate secrecy | Foreign and absent IDs receive equal detail/update/delete/restore/purge/assign responses for admin and ordinary users across clients/leads/projects. Bulk requests preserve foreign rows. Foreign additions do not alter 13 list/search/report responses. |
| Existing broader isolation | `test_security_boundaries.py`, service test modules, storage/import/user/preference/identity and platform-job tests cover tenant FKs/parents, roles/inactive identities, bounded import, exports, object paths, file metadata and platform denial. |

The policy verifier reads catalogs only. All intentional policy mutations run in
fresh `security_test_*` schemas owned by the fixture and removed after tests; never
against `public`. Run the full existing CI suite with PostgreSQL, RLS enabled and the
restricted test role. A SQLite-only run necessarily skips the PostgreSQL proofs.
The verifier is an explicit CI/rollout attestation, not a per-request query or a
continuous production monitor. Reviewed PostgreSQL policy deparse expressions are
strict by design; a database-version representation change requires review.

## Gate reconciliation and remaining work

The existing web implementation now has explicit service/identity boundaries and
an executable table, predicate and policy proof inventory. This closes the named
current-web proof gaps; it does not certify every future Gate 2/3 requirement.
Delegated connection/grant identity, revoked/inactive grants and scope enforcement
are Gate 4 work and need their own negative tests before an MCP pilot.

Global account-number uniqueness and polymorphic activity references remain model
design issues. Durable file storage/reconciliation is still required before exposing
file tools; staging uses ephemeral local storage. Platform jobs remain disabled,
and real backup/restore reliability, protected queues and restore approval are
separate operational work. The earlier intermittent staging database stall remains
open. No production/frontend change, migration, capacity increase or real platform
operation is part of this increment.

## Validation and rollout

Rollout evidence will be recorded here after PostgreSQL CI and disposable staging
verification. Rollback target is staging v35 application
`6c8ce189d42e9e06be73a8bee59b418a561801a7`:
`registry.fly.io/pathsixsolutions-backend-staging:6c8ce189d42e9e06be73a8bee59b418a561801a7`.
