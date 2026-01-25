from quart import Blueprint, request, jsonify, g, Response
import pandas as pd
import io
import json
from datetime import datetime
from app.models import Lead, User
from app.database import SessionLocal
from app.utils.auth_utils import requires_auth
from app.utils.phone_utils import clean_phone_number
from app.utils.email_utils import send_email
from app.constants import PHONE_LABELS

imports_bp = Blueprint("imports", __name__, url_prefix="/api/import")

VALID_LEAD_FIELDS = {
    'name': {'required': True, 'type': 'string', 'max_length': 100},
    'contact_person': {'required': False, 'type': 'string', 'max_length': 100},
    'contact_title': {'required': False, 'type': 'string', 'max_length': 100},
    'email': {'required': False, 'type': 'email', 'max_length': 120},
    'phone': {'required': False, 'type': 'phone', 'max_length': 20},
    'phone_label': {'required': False, 'type': 'choice', 'choices': PHONE_LABELS},
    'secondary_phone': {'required': False, 'type': 'phone', 'max_length': 20},
    'secondary_phone_label': {'required': False, 'type': 'choice', 'choices': PHONE_LABELS},
    'address': {'required': False, 'type': 'string', 'max_length': 255},
    'city': {'required': False, 'type': 'string', 'max_length': 100},
    'state': {'required': False, 'type': 'string', 'max_length': 100},
    'zip': {'required': False, 'type': 'string', 'max_length': 20},
    'notes': {'required': False, 'type': 'text'},
    'type': {'required': False, 'type': 'string'},  # Permissive - accepts any value
    'lead_status': {'required': False, 'type': 'string'}  # Permissive - accepts any value
}

def read_file(file_storage):
    filename = file_storage.filename.lower()
    if filename.endswith(".csv"):
        for encoding in ["utf-8", "latin1", "cp1252"]:
            try:
                return pd.read_csv(file_storage.stream, encoding=encoding)
            except UnicodeDecodeError:
                continue
        raise ValueError("Could not decode CSV file")
    elif filename.endswith(".xlsx"):
        return pd.read_excel(file_storage.stream)
    else:
        raise ValueError("Unsupported file format")

@imports_bp.route("/leads/preview", methods=["POST"])
@requires_auth()
async def preview_leads():
    files = await request.files
    if 'file' not in files:
        return jsonify({"error": "No file uploaded"}), 400

    file = files['file']
    try:
        df = read_file(file)
        df.columns = df.columns.astype(str).str.strip()

        return jsonify({
            "headers": df.columns.tolist(),
            "rows": df.head(10).fillna('').values.tolist(),
            "totalRows": len(df)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@imports_bp.route("/leads/submit", methods=["POST"])
@requires_auth()
async def submit_leads():
    user = request.user
    form = await request.form
    files = await request.files
    successful_leads = []

    if 'file' not in files:
        return jsonify({"error": "No file uploaded"}), 400

    file = files['file']
    assigned_email = form.get("assigned_user_email")
    column_mappings = json.loads(form.get("column_mappings", "[]"))

    session = SessionLocal()
    try:
        assigned_user = session.query(User).filter_by(
            email=assigned_email,
            tenant_id=user.tenant_id,
            is_active=True
        ).first()
        if not assigned_user:
            return jsonify({"error": "Assigned user not found or inactive"}), 400

        df = read_file(file)
        df.columns = df.columns.astype(str).str.strip()

        mapped_fields = [m['leadField'] for m in column_mappings if m['leadField']]
        if 'name' not in mapped_fields:
            return jsonify({"error": "'name' field (Company Name) is required"}), 400

        successful = 0
        failed = 0
        failures = []
        warnings = []

        for idx, row in df.iterrows():
            try:
                lead_data = {}
                for mapping in column_mappings:
                    csv_col = mapping['csvColumn']
                    lead_field = mapping['leadField']
                    if not lead_field:  # Skip unmapped fields
                        continue

                    val = row.get(csv_col, '')
                    if pd.isna(val) or str(val).strip() == '':
                        continue

                    cleaned = str(val).strip()
                    # ... cleaning logic here ...
                    lead_data[lead_field] = cleaned

                    if lead_field in ['phone', 'secondary_phone']:
                        cleaned = clean_phone_number(cleaned)
                        if not cleaned:
                            warnings.append(f"Invalid phone on row {idx + 2}")
                            continue
                    elif lead_field == 'email':
                        cleaned = cleaned.lower()
                    # type and lead_status are permissive - accept any value
                    elif lead_field.endswith("_label") and cleaned.lower() not in [p.lower() for p in PHONE_LABELS]:
                        warnings.append(f"Unknown phone label '{cleaned}' on row {idx + 2}")
                        cleaned = "work"

                    lead_data[lead_field] = cleaned

                if not lead_data.get("name"):
                    raise ValueError("Missing required 'name' field")

                lead_data.setdefault("type", "None")
                lead_data.setdefault("lead_status", "open")
                if "phone" in lead_data and "phone_label" not in lead_data:
                    lead_data["phone_label"] = "work"
                if "secondary_phone" in lead_data and "secondary_phone_label" not in lead_data:
                    lead_data["secondary_phone_label"] = "mobile"

                lead = Lead(
                    tenant_id=user.tenant_id,
                    created_by=user.id,
                    assigned_to=assigned_user.id,
                    created_at=datetime.utcnow(),
                    **lead_data
                )
                successful_leads.append(lead_data.copy())
                session.add(lead)
                session.flush()
                successful += 1
            except Exception as e:
                failed += 1
                failures.append({
                    "row": idx + 2,
                    "data": row.dropna().to_dict(),
                    "error": str(e)
                })
                session.rollback()

        if successful:
            session.commit()

        if successful:
            lead_list = [f"- {ld['name']}" for ld in successful_leads if 'name' in ld]
            summary = "\n".join(lead_list[:10])  # limit preview to 10
            more = f"\n...and {len(lead_list) - 10} more." if len(lead_list) > 10 else ""

            body = (
                f"You've been assigned {successful} new leads from a recent import by {user.email}.\n\n"
                f"Sample of assigned leads:\n{summary}{more}\n\n"
                "Please log in to the CRM to view all your leads."
            )
            try:
                await send_email(
                    subject="New Leads Assigned to You",
                    recipient=assigned_user.email,
                    body=body
                )
            except Exception as e:
                print(f"Failed to send summary email: {e}")

        return jsonify({
            "message": f"Import complete: {successful} succeeded, {failed} failed.",
            "successful_imports": successful,
            "failed_imports": failed,
            "warnings": list(set(warnings)),
            "failures": failures
        })
    except Exception as e:
        session.rollback()
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500
    finally:
        session.close()


@imports_bp.route("/leads/template", methods=["GET"])
@requires_auth()
async def get_lead_template():
    headers = [
        "Company Name", "Contact Person", "Contact Title", "Email", "Phone",
        "Phone Label", "Secondary Phone", "Secondary Phone Label", "Address",
        "City", "State", "Zip", "Notes", "Type", "Lead Status"
    ]
    csv_data = ",".join(headers) + "\n"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=lead_import_template.csv"}
    )
