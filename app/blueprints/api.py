from flask import Blueprint, jsonify, g

from app.database import get_share_by_code, get_share_status

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.route("/share/<code>/status")
def share_status(code):
    db = g.db
    share = get_share_by_code(db, code)
    if not share:
        return jsonify(valid=False), 200
    status = get_share_status(share)
    return jsonify(valid=status == "active", status=status), 200
