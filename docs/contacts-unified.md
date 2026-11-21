# Contacts in one place — November 20, 2026

Branch `contacts-cleanup` (backend and frontend). Approved by the user on
November 20, 2026, after the AI connector was declared a production beta.

## What changed

Before, a person could live in four places: a client's or lead's own
`contact_person`/`email`/`phone` columns, the `contacts` table (extra people
only), or a project's `primary_contact_*` copy. Now every person is a row in
`contacts`, which belongs to exactly one client, lead or standalone project.

| Piece | How it works now |
| --- | --- |
| Client and lead person fields | `contact_person`, `contact_title`, `email`, `phone`, `phone_label`, `secondary_phone` and `secondary_phone_label` read and write the record's **primary contact** (`contacts.is_primary`, at most one per record). The API, the web forms and the AI connector keep the same field names. |
| Projects | `projects.primary_contact_id` points at one contact. Under a client or lead it is one of that record's contacts, matched by email, then name, otherwise added there. A standalone project owns its contact. `primary_contact_*` fields read and write through that link. Clearing them all unlinks the contact and deletes a project-owned one. |
| Extra contacts | Unchanged for the web Contacts tab, the contacts API and the AI contact tools. They still list only the non-primary people. Primary and project-owned people are edited through their record. |
| Lead conversion | Creating a client with `source_lead_id` now moves every contact of the lead to the client, primary included, in the same transaction. Details sent with the conversion only fill gaps. Before, the lead's extra contacts stayed on the deleted lead, invisible, and its title and secondary phone were dropped. |
| Permanent deletion | A record's own primary contact is deleted with it, since it used to be the record's own columns. Extra contacts still block, as before. |
| Directory | `GET /api/contacts/directory?page=&per_page=&q=` lists every visible person alphabetically with their record: `{type: client\|lead\|project, id, name}`. People whose record is deleted are left out, which hides converted leads. The web page is `/contacts`. |

The model code is in `app/models.py` (`PrimaryContactFields`,
`ProjectContactFields`, `resolve_contacts`). `storage_column()` maps a person
field to the contact column that stores it, for length checks and tool schemas.

## Migration `contacts_unified`

It runs in one transaction and refuses to run under row security
(`row_security = off`), so it can never copy a filtered subset. In order:

1. Adds `contacts.project_id` and `contacts.is_primary`, plus `projects.primary_contact_id`. Same-tenant composite foreign keys are added for both links. The project link uses `ON DELETE SET NULL (primary_contact_id)`, which never clears `tenant_id`.
2. Replaces `ck_contacts_one_parent` with `num_nonnulls(client_id, lead_id, project_id) = 1`, and adds one partial unique index per parent kind for primaries.
3. Moves contacts stranded on converted, deleted leads to the client the lead became.
4. Makes each client's and lead's own person its primary contact. If the record already has that person (same email, or same full name, ignoring case), the existing person is promoted and only missing details are filled. Otherwise a new contact is created.
5. Links each project to its person in the same way.

**Old columns are not dropped.** The application no longer reads or writes
them, so they are a frozen backup of the values at migration time. Downgrade is
deliberately refused, because edits made after the migration live only in
`contacts`. Dropping the old columns is a separate, later migration, listed
under Future consideration in `mcp-readiness-status.md`.

## Verification

- `tests/test_contacts_unified.py` (crm tier): field translation in normal and restricted (row security) sessions, clearing a person, conversion moving people without duplicates, project matching, standalone projects through conversion, directory order, search, paging and access, contacts API boundaries, and purge.
- `tests/test_contacts_unified_migration.py` (migration tier): backfill against the old table layout with promotion, gap-filling, stranded people, projects, database rules and atomic failure.
- Existing core, crm and isolation tiers pass unchanged. That covers the AI connector's create and update tools, which still send `contact_person`.
- Local browser check against a throwaway database: the Contacts page (desktop, phone width, search), lead-to-client conversion (both people moved, none duplicated) and project-to-client conversion.

## Staging rollout

Deploy the branch, then run the migration from the new image. The
administrator URL comes from the workstation helper:

    flyctl deploy --app pathsixsolutions-backend-staging --config fly.staging.toml
    powershell -File G:/Projects/tools/pathsix-staging-admin-url.ps1 | flyctl ssh console --app pathsixsolutions-backend-staging -C "python /app/scripts/migrate_staging_membership.py --target staging --revision contacts_unified"
    (same with --apply)

The trial run prints the counts it would move, promote and create, then rolls
back. Production is unchanged and needs the user's decision.

## Reconciling with the mobile branch

Decided by the user on November 20, 2026: the phone receives the `contacts`
table as it really is, **primary contacts included**, flagged with
`is_primary`. The mobile Contacts screen then lists everyone, as the new web
page does. Whichever of `mobile` and `contacts-cleanup` merges second does
these steps. This branch is `contacts-cleanup`, PR boonewh/pathsix-backend#35.

1. **Migration order.** Both branches' first migration builds on
   `mcp_record_updates`. Repoint the second branch's first migration
   (`mobile_stable_ids` or `contacts_unified`) at the other branch's last one.
   Then add the other branch's columns to its frozen table lists where they
   matter. For example, `mobile_stable_ids`, `mobile_change_tracking` and
   `mobile_tombstones` already cover the `contacts` table. Check that the new
   `contacts.project_id` and `is_primary` columns need no extra handling there.
2. **Sync feed fields.** In `sync_feed.py` `FIELDS['contacts']`, add
   `project_id` (parent UUID, like `client_id`/`lead_id`) and `is_primary`. The
   client, lead and project fields (`contact_person`, `email`,
   `primary_contact_*`) need no change. They are now model properties that read
   the primary or linked contact, and `getattr(model, field)` in `select()`
   compiles them to a subquery. Do not read them through `__table__.c`, since
   those columns are the frozen backup. Use `app.models.storage_column()` when
   a column object is needed.
3. **Contacts visibility.** `_visible('contacts')` and `_access_changed` cover
   client and lead contacts. Add project-owned contacts (`project_id` set) with
   the same project access the projects feed uses. Otherwise standalone
   projects' people never reach the phone.
4. **Change detection.** Editing a client's or lead's person now changes a
   `contacts` row, not the parent row. When a contact with `is_primary` changes,
   also bump the sync marker of its client or lead. When any contact changes,
   also bump every project whose `primary_contact_id` points at it, since those
   records' synced contact fields changed. A trigger on `contacts`, next to the
   existing per-table ones, is the natural place.
5. **Phone writes (F-14 edit sync).** A phone edit to `contact_person`,
   `email`, `phone` or the other person fields of a client or lead, or to
   `primary_contact_*` on a project, must go through model attributes or the
   existing services, which handle creating, updating and linking the contact.
   Base-version conflict checks for those fields must compare the values read
   through the model (the contact's current values), not the frozen columns.
   Writes to primary or project-owned contacts through the contacts entity
   should be refused or routed to the parent record, as `ContactService` does.
6. **Mobile app.** On the Contacts screen, show each person's record ("Client:
   ACME Bricks", "Lead: …", "Project: …") and mark the primary contact. The
   client, lead and project screens can keep showing `contact_person` and the
   rest, which stay correct.
7. **Expected text conflicts** are in `app/services/contacts.py` and
   `app/services/purge.py`. Keep both sides' intent: the cleanup's primary and
   project filters, and mobile's tombstones and markers.

Tests to keep green after reconciling are `tests/test_contacts_unified.py` and
`tests/test_contacts_unified_migration.py`, plus the mobile sync suites.
