# Subscription service — 2026-09-12

SubscriptionService owns list/detail/create/update/delete/renew database work.
The thin HTTP adapter validates input and commits mutations. Services bind the
trusted Principal, scope root/relationship queries and flush without committing.
Every operation requires an accessible active client, using the same shared
creator-or-assignee/admin rules as accounts. Lists use the same predicate.

## Permission correction

Previously ordinary users were restricted in lists but could create, fetch, edit,
delete or renew subscriptions for any active client in their tenant by ID. Those
operations now enforce inherited client access too. Admins no longer see or operate
on subscriptions with deleted or malformed foreign parents. Foreign/inaccessible
record operations return 404; filtered lists return only visible records.

## Behavior and validation

Response fields, renewal ordering, monthly/yearly date arithmetic, explicit renewal
overrides, nullable renewal dates and cancellation/reactivation behavior remain.
Renew continues to advance one billing cycle and reactivate paused/cancelled rows.
It is not an idempotent operation and must not be automatically retried as a write.
Subscription updates still do not support moving to another client. This service
records CRM subscription data; it does not initiate payment processing.

Malformed bodies, invalid filter IDs/statuses, null required update fields,
non-finite prices and out-of-range calculated renewals now return 400 before writes.
Input dates normalize UTC offsets before storage/serialization. The current frontend
already supplies numeric client IDs and omits blank renewal dates.

## Validation

Source commit fe59185. Local suite: 162 passed, 29 PostgreSQL-only skipped
(61.61 seconds). Focused subscription PostgreSQL suite: 24 passed (26.72 seconds)
from an isolated temporary checkout at /tmp/crm-subscription-validation on the
existing staging machine, using the restricted runtime role for HTTP checks and
forced RLS fixture policies. Test fixture wiring now includes subscriptions.
This is not a full PostgreSQL suite and not a live deployed endpoint check.

The original deployment and one retry both stalled at "Waiting for depot builder"
before any image build output. Both exact local flyctl deploy processes were stopped.
No existing alternative builder was found; no additional builder/app machine was
created. The new five source/test files were uploaded only to the temporary test
checkout. /app and the live staging deployment were not modified.

Staging remains v26 / d07e3ca, independently confirmed by Fly releases and live
APP_REVISION. Before/after isolated tests: 15 connections, zero security_test schemas
and zero public subscriptions. No new matching connection errors appeared in the
filtered Fly database log stream. The earlier intermittent disconnect cause remains
unresolved. Production/frontend deployments and resource sizes/count remain unchanged.

Next required action: deploy fe59185 (or a descendant containing it) to the existing
staging backend once the build worker is available, then run live subscription
create/list/detail/edit/cancel/renew/delete and cleanup checks. No migration is needed.
Do not describe this milestone as deployed or the live smoke as passed. Subsequent
service work should address Recent Activity access after record reassignment, then
remaining reports/user/storage/import/conversion and job contexts. No AI access is
enabled.
