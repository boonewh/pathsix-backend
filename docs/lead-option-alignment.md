# Lead option alignment

The lead editor previously required exact configured status and business-type strings.
Existing case/whitespace variants, retired choices, and blank values could prevent
unrelated edits. Two schema tests also asserted fixed global enums even though
backend schemas intentionally permit tenant-specific values.

## Behavior

- New leads and CSV imports default to the tenant's first configured status, or
  `open` when unavailable. An unspecified business type retains the `None` sentinel;
  the app never silently assigns the first industry to an unclassified lead.
- Explicit status/type writes canonicalize case and surrounding whitespace against
  that tenant's choices. Unknown strings remain accepted, preserving the existing
  API contract. Different names such as `converted` and `won` are not treated as aliases.
- The frontend shows a matching canonical choice or an existing-value option.
  Unchanged status/type/source fields are omitted on update, preserving original
  blanks and historical spellings during unrelated edits.
- Default configuration templates now use open, qualified, proposal, won, lost,
  matching the existing report convention. Existing tenant configs are untouched.
- The lead card uses the same single edit modal as the table. Form state stays local
  until submission instead of syncing competing forms into parent state.

## Validation and release boundary

Backend regressions exercise tenant-scoped defaults, explicit normalization, custom
values, note-only preservation, conversion timestamps, and CSV imports. Browser
regressions exercise legacy/blank edits, custom defaults, and intentional changes.
Both CI workflows include the relevant lead regressions.

This change has no schema migration or production data cleanup. Existing reports
still use `won` as their conversion status; arbitrary custom terminal statuses need
an explicit reporting design, not an inferred rewrite of customer data.

Ship backend and frontend application changes together through the normal release
workflow. The provisioning and seed template edits affect future use of those
scripts only; do not run provisioning or seeding against existing production data
as part of this release.
