"""Shared HTTP adapter for the three Trash deletion flows."""
from quart import jsonify, request
from sqlalchemy.exc import IntegrityError

from app.services.errors import RecordNotFound
from app.services.purge import PurgeConflict, PurgeService, database_conflict_payload


async def purge_response(session_factory, resource, record_id=None):
    if record_id is None:
        raw = await request.get_json()
        if not isinstance(raw, dict):
            return jsonify({"error": "Invalid request body"}), 400
        ids = raw.get(f"{resource[:-1]}_ids")
    else:
        ids = [record_id]
    with session_factory() as session:
        try:
            result = PurgeService(session, request.principal).purge(
                resource, ids, single=record_id is not None
            )
            session.commit()
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store"
            return response
        except PurgeConflict as exc:
            session.rollback()
            return jsonify(exc.payload()), 409
        except IntegrityError as exc:
            session.rollback()
            if getattr(exc.orig, "pgcode", None) not in {"23503", "23514"}:
                raise
            return jsonify(database_conflict_payload(exc)), 409
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
