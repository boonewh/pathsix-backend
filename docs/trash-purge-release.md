# Trash purge release — 2026-09-22

Release base: backend origin/main 81fa52b and frontend origin/main 4785ef8.
The already-running auth-diagnostics hotfix (original 6c20e17) is preserved.
No staging-only service migration, schema migration, or resource resizing is included.

Single and bulk permanent deletion for clients, leads, and projects share one
admin-only, explicitly tenant-scoped service. It locks eligible parents, counts
visible linked records (including soft-deleted children), and returns named
409 blockers. The entire eligible batch stays intact on conflict. Successful
responses identify actual deleted IDs. Database constraints remain the final
protection; restricted/unseen links receive a category-only safe conflict.
No children are automatically detached or deleted.

The frontend confirms permanent deletion and offers Keep in Trash, Restore and
review, and Refresh Trash. Failed records stay visible and selected. It uses one
bulk request for each record type, suppresses duplicate toasts, and preserves the
production read-activity/recovery behavior. Network ambiguity prompts refresh.

Validation so far: 21 purge/backend regression cases passed locally; 21 frontend
browser checks passed (14 purge plus existing read-recovery and sales-activity).
Frontend typecheck/build passed. Full backend local suite: 45 passed, 2 failed.
Both failures in tests/test_lead_schemas.py reproduce on the unchanged production
baseline (5 passed, 2 failed there); they concern pre-existing schema validation.
CI adds isolated PostgreSQL 18 coverage for purge and production recovery tests.

Deploy backend before merging the frontend to main (Vercel deploys main).
Do not test permanent deletion of customer records as a production smoke check.
