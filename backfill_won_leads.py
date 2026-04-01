"""
One-time backfill script: mark historically converted leads as 'won'

PROBLEM
-------
Before the convert-to-client flow was updated, leads were soft-deleted when
converted but never marked lead_status='won'. This means conversion history
is invisible to reports. This script reconstructs that history.

WHAT IT DOES
------------
1. Finds all soft-deleted leads where lead_status != 'won' (tenant 1)
2. Tries to match each to a client by name (case-insensitive exact match)
3. For confirmed matches:
   - Sets lead.lead_status = 'won'
   - Sets lead.converted_on = lead.deleted_at (best available timestamp)
   - Sets client.source_lead_id = lead.id (if not already set)
   - Sets client.converted_on = lead.deleted_at (if not already set)
4. Prints a full report: matched, skipped, unmatched (for manual review)

SAFE TO RUN
-----------
- Dry run mode (default) prints what WOULD happen without changing anything
- Set DRY_RUN = False to actually apply changes
- Everything runs in a single transaction — rolls back on any error
- Safe to re-run: already-won leads and already-linked clients are skipped

HOW TO RUN
----------
    flyctl ssh console -a pathsixsolutions-backend

    # Dry run first (review output before committing)
    DATABASE_URL="postgres://..." /venv/bin/python backfill_won_leads.py

    # Apply changes
    DRY_RUN=false DATABASE_URL="postgres://..." /venv/bin/python backfill_won_leads.py
"""

import os
import sys
import psycopg2
import psycopg2.extras

DB_URL = os.environ.get("DATABASE_URL", "")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"
TENANT_ID = 1


def main():
    if not DB_URL:
        print("ERROR: DATABASE_URL not set.")
        sys.exit(1)

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    mode = "DRY RUN (no changes will be made)" if DRY_RUN else "LIVE — changes will be committed"
    print(f"=== Backfill Won Leads — {mode} ===\n")

    try:
        # ── Step 1: Get all soft-deleted, non-won leads for this tenant ──────
        cur.execute("""
            SELECT id, name, lead_status, deleted_at, converted_on
            FROM leads
            WHERE tenant_id = %s
              AND deleted_at IS NOT NULL
              AND lead_status != 'won'
            ORDER BY deleted_at
        """, (TENANT_ID,))
        deleted_leads = cur.fetchall()

        print(f"Soft-deleted leads without 'won' status: {len(deleted_leads)}\n")

        if not deleted_leads:
            print("Nothing to backfill.")
            return

        # ── Step 2: Get all clients for this tenant ───────────────────────────
        cur.execute("""
            SELECT id, name, source_lead_id, converted_on
            FROM clients
            WHERE tenant_id = %s
              AND deleted_at IS NULL
            ORDER BY id
        """, (TENANT_ID,))
        clients = cur.fetchall()

        # Build lookup: lowercase name → list of clients (handle duplicates)
        client_by_name: dict = {}
        for c in clients:
            key = c["name"].strip().lower()
            if key not in client_by_name:
                client_by_name[key] = []
            client_by_name[key].append(c)

        # ── Step 3: Match and categorize ─────────────────────────────────────
        matched = []
        ambiguous = []
        unmatched = []

        for lead in deleted_leads:
            key = lead["name"].strip().lower()
            matches = client_by_name.get(key, [])

            if len(matches) == 1:
                matched.append((lead, matches[0]))
            elif len(matches) > 1:
                ambiguous.append((lead, matches))
            else:
                unmatched.append(lead)

        # ── Step 4: Print summary ─────────────────────────────────────────────
        print(f"{'─'*60}")
        print(f"  Matched (will be marked won):    {len(matched)}")
        print(f"  Ambiguous (multiple clients):    {len(ambiguous)}")
        print(f"  Unmatched (no client found):     {len(unmatched)}")
        print(f"{'─'*60}\n")

        # ── Step 5: Show and apply matched ────────────────────────────────────
        if matched:
            print("MATCHED LEADS:")
            for lead, client in matched:
                converted_on = lead["deleted_at"]
                lead_already_linked = client["source_lead_id"] is not None
                action = "SKIP (client already linked)" if lead_already_linked else "UPDATE"
                print(f"  [{action}] Lead '{lead['name']}' (id={lead['id']}) "
                      f"→ Client id={client['id']} | converted ~{converted_on.date() if converted_on else 'unknown'}")

                if not DRY_RUN and not lead_already_linked:
                    # Mark lead as won
                    cur.execute("""
                        UPDATE leads
                        SET lead_status = 'won',
                            converted_on = COALESCE(converted_on, %s)
                        WHERE id = %s
                    """, (converted_on, lead["id"]))

                    # Link client back to lead
                    cur.execute("""
                        UPDATE clients
                        SET source_lead_id = %s,
                            converted_on = COALESCE(converted_on, %s)
                        WHERE id = %s
                    """, (lead["id"], converted_on, client["id"]))

        # ── Step 6: Handle ambiguous — mark lead won, link to first client ───
        if ambiguous:
            print("\nAMBIGUOUS (multiple clients — marking lead won, linking to first client):")
            for lead, matches in ambiguous:
                client = matches[0]
                converted_on = lead["deleted_at"]
                client_ids = [str(c["id"]) for c in matches]
                print(f"  [UPDATE] Lead '{lead['name']}' (id={lead['id']}) "
                      f"→ Client id={client['id']} (others: {', '.join(client_ids[1:])}) "
                      f"| converted ~{converted_on.date() if converted_on else 'unknown'}")

                if not DRY_RUN:
                    cur.execute("""
                        UPDATE leads
                        SET lead_status = 'won',
                            converted_on = COALESCE(converted_on, %s)
                        WHERE id = %s
                    """, (converted_on, lead["id"]))

                    if client["source_lead_id"] is None:
                        cur.execute("""
                            UPDATE clients
                            SET source_lead_id = %s,
                                converted_on = COALESCE(converted_on, %s)
                            WHERE id = %s
                        """, (lead["id"], converted_on, client["id"]))

        # ── Step 7: Show unmatched (likely manually deleted leads) ────────────
        if unmatched:
            print("\nUNMATCHED (no client found — likely manually deleted, not converted):")
            for lead in unmatched:
                print(f"  Lead '{lead['name']}' (id={lead['id']}) "
                      f"deleted {lead['deleted_at'].date() if lead['deleted_at'] else 'unknown'}")

        # ── Step 8: Commit or rollback ────────────────────────────────────────
        if DRY_RUN:
            conn.rollback()
            print(f"\nDry run complete — no changes made.")
            print(f"To apply: DRY_RUN=false DATABASE_URL=\"...\" /venv/bin/python backfill_won_leads.py")
        else:
            conn.commit()
            actually_updated = sum(1 for lead, client in matched if client["source_lead_id"] is None)
            ambiguous_updated = len(ambiguous)
            total_won = actually_updated + ambiguous_updated
            print(f"\nCommitted. {total_won} leads marked won ({actually_updated} matched + {ambiguous_updated} ambiguous).")

    except Exception as e:
        conn.rollback()
        print(f"\nERROR: {e}")
        print("Rolled back — nothing changed.")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
