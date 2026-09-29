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

The full local suite passed **396 tests**, with **49 PostgreSQL-only skips**; the
final focused proof suite passed **31**, with **19 PostgreSQL-only skips**.
[PostgreSQL 18/RLS CI](https://github.com/boonewh/pathsix-backend/actions/runs/36635630754)
passed **445 tests with zero skips**, including all deliberate-failure proofs.
All **50 focused tests passed** against the deployed staging code in disposable
PostgreSQL schemas, including all 56 policy injections. The live public catalog
passed the read-only check before and after: **14 tables, 20 policies**, restricted
runtime credentials and RLS enabled. Counts and full-row digests across all **17
public application tables** were identical; no test schemas remained. Platform
operations remained disabled and were checked only through their guarded denials.

Live HTTP login/protected reads, current identity/configuration response compatibility,
no-store headers, malformed/wrong-current-password rejection and the unregistered
backup API all passed. The identity/configuration fingerprint was unchanged.

Application `898cc7a17b604502b95568f45e7b375048ee8702`, built from a clean Git archive,
runs as **staging v36**. Image digest:
`sha256:b928dd3da9fe0209aa9d24e1098d49d000c2d321b46790dce1672cc7089a7226`.
Both existing machines passed health checks and retained their capacity, services
and mounts. All 21 original dirty files and the three original checkout revisions
were preserved. The final documentation commit changes no deployed application,
test, migration or operations source. The Windows SSH wrapper emitted its known
post-output handle warning; pytest, both snapshots and the process exit confirmed
successful completion.

Rollback target is staging v35 application
`6c8ce189d42e9e06be73a8bee59b418a561801a7`:
`registry.fly.io/pathsixsolutions-backend-staging:6c8ce189d42e9e06be73a8bee59b418a561801a7`.
