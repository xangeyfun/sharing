import secrets
import time
import string
import sqlite3
from functools import wraps
from datetime import datetime, timezone, timedelta

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, session, g, abort
)
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
import requests as http_requests

from app.config import Config
from app.database import (
    get_share_by_code, get_share_by_id, get_share_status,
    list_shares, get_stats, get_activity_log, log_activity,
    delete_share, update_share, create_share, create_twofa_code,
    verify_twofa_code, consume_twofa_code, cleanup_expired_twofa_codes,
)
from app.storage import (
    init_storage, save_file, delete_file, validate_upload_filename,
)
from app.ratelimit import rate_limit_auth, rate_limit_2fa, rate_limit_upload

admin_bp = Blueprint("admin", __name__)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("admin.login"))
        return f(*args, **kwargs)
    return decorated


def generate_2fa_code():
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(Config.TWO_FA_CODE_LENGTH))


def send_2fa_discord(code):
    webhook_url = Config.DISCORD_WEBHOOK_URL
    if not webhook_url:
        return False
    embed = {
        "title": "Authentication Code",
        "description": f"Your temporary 2FA code is:\n\n```\n{code}\n```\n\nThis code expires in {Config.TWO_FA_CODE_LIFETIME // 60} minutes.",
        "color": 0x2563EB,
    }
    payload = {"embeds": [embed]}
    try:
        resp = http_requests.post(webhook_url, json=payload, timeout=10)
        return resp.status_code in (200, 204)
    except Exception:
        return False


@admin_bp.route("/admin/login", methods=["GET", "POST"])
@rate_limit_auth
def login():
    if session.get("authenticated"):
        return redirect(url_for("admin.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Username and password required", "error")
            return render_template("admin/login.html")

        if username != Config.ADMIN_USERNAME:
            time.sleep(0.5)
            flash("Invalid credentials", "error")
            return render_template("admin/login.html")

        ph = PasswordHasher()
        try:
            ph.verify(Config.ADMIN_PASSWORD_HASH, password)
        except VerifyMismatchError:
            time.sleep(0.5)
            flash("Invalid credentials", "error")
            return render_template("admin/login.html")
        except Exception:
            time.sleep(0.5)
            flash("Invalid credentials", "error")
            return render_template("admin/login.html")

        code = generate_2fa_code()
        code_hash = ph.hash(code)
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=Config.TWO_FA_CODE_LIFETIME)).isoformat()

        db = g.db
        cleanup_expired_twofa_codes(db)
        code_id = create_twofa_code(db, code_hash, expires_at)

        session["pending_2fa_code_id"] = code_id

        sent = send_2fa_discord(code)
        if not sent:
            db.execute("UPDATE twofa_codes SET used = 1 WHERE id = ?", (code_id,))
            db.commit()
            session.pop("pending_2fa_code_id", None)
            flash("Failed to send 2FA code. Check Discord webhook.", "error")
            return render_template("admin/login.html")

        return redirect(url_for("admin.verify_2fa"))

    return render_template("admin/login.html")


@admin_bp.route("/admin/verify-2fa", methods=["GET", "POST"])
@rate_limit_2fa
def verify_2fa():
    if session.get("authenticated"):
        return redirect(url_for("admin.dashboard"))

    code_id = session.get("pending_2fa_code_id")
    if not code_id:
        return redirect(url_for("admin.login"))

    if request.method == "POST":
        entered_code = request.form.get("code", "").strip().upper()

        if not entered_code or len(entered_code) != Config.TWO_FA_CODE_LENGTH:
            flash("Invalid code format", "error")
            return render_template("admin/twofa.html")

        db = g.db
        allowed = verify_twofa_code(db, code_id, Config.TWO_FA_MAX_ATTEMPTS)
        if not allowed:
            session.pop("pending_2fa_code_id", None)
            flash("Invalid or expired code", "error")
            return redirect(url_for("admin.login"))

        row = db.execute(
            "SELECT code_hash FROM twofa_codes WHERE id = ?", (code_id,)
        ).fetchone()
        if not row:
            session.pop("pending_2fa_code_id", None)
            flash("Code expired", "error")
            return redirect(url_for("admin.login"))

        ph = PasswordHasher()
        try:
            ph.verify(row["code_hash"], entered_code)
        except VerifyMismatchError:
            flash("Incorrect code", "error")
            return render_template("admin/twofa.html")
        except Exception:
            flash("Invalid code", "error")
            return render_template("admin/twofa.html")

        consume_twofa_code(db, code_id)
        session.clear()
        session["authenticated"] = True
        session["authenticated_at"] = time.time()
        session.permanent = True

        return redirect(url_for("admin.dashboard"))

    return render_template("admin/twofa.html")


@admin_bp.route("/admin/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Signed out", "success")
    return redirect(url_for("admin.login"))


@admin_bp.route("/admin")
@login_required
def dashboard():
    db = g.db
    stats = get_stats(db)
    recent_shares, _ = list_shares(db, limit=6, offset=0)
    for s in recent_shares:
        s["status"] = get_share_status(s)
    recent_activity = get_activity_log(db, limit=10)
    return render_template("admin/dashboard.html",
        stats=stats,
        recent_shares=recent_shares,
        recent_activity=recent_activity,
    )


@admin_bp.route("/admin/shares")
@login_required
def shares_list():
    db = g.db
    status = request.args.get("status", "")
    search = request.args.get("search", "")
    sort_by = request.args.get("sort", "created_at")
    sort_dir = request.args.get("dir", "DESC")
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    per_page = 20
    offset = (page - 1) * per_page

    shares, total = list_shares(
        db, status_filter=status if status else None,
        search=search if search else None,
        sort_by=sort_by, sort_dir=sort_dir,
        limit=per_page, offset=offset,
    )

    for s in shares:
        s["status"] = get_share_status(s)

    total_pages = max(1, (total + per_page - 1) // per_page)

    return render_template("admin/shares.html",
        shares=shares, total=total, page=page,
        total_pages=total_pages, status=status, search=search,
        sort_by=sort_by, sort_dir=sort_dir,
    )


@admin_bp.route("/admin/shares/create", methods=["GET", "POST"])
@login_required
@rate_limit_upload
def create_share_view():
    if request.method == "POST":
        db = g.db
        file = request.files.get("file")
        custom_code = request.form.get("custom_code", "").strip()
        expires_days = request.form.get("expires_days", "").strip()
        expires_hours = request.form.get("expires_hours", "").strip()
        download_limit = request.form.get("download_limit", "").strip()
        view_limit = request.form.get("view_limit", "").strip()

        if not file or not file.filename:
            flash("No file selected", "error")
            return render_template("admin/create_share.html")

        valid, err = validate_upload_filename(file.filename)
        if not valid:
            flash(err, "error")
            return render_template("admin/create_share.html")

        if custom_code:
            if len(custom_code) < Config.MIN_CUSTOM_CODE_LENGTH:
                flash(f"Custom code must be at least {Config.MIN_CUSTOM_CODE_LENGTH} characters", "error")
                return render_template("admin/create_share.html")
            if len(custom_code) > Config.MAX_CUSTOM_CODE_LENGTH:
                flash(f"Custom code must be at most {Config.MAX_CUSTOM_CODE_LENGTH} characters", "error")
                return render_template("admin/create_share.html")
            allowed_chars = set(string.ascii_letters + string.digits + "-_")
            if not all(c in allowed_chars for c in custom_code):
                flash("Custom code can only contain letters, numbers, hyphens and underscores", "error")
                return render_template("admin/create_share.html")

            reserved = {"admin", "api", "static", "s", "login", "logout", "verify-2fa"}
            if custom_code.lower() in reserved:
                flash("This code is reserved", "error")
                return render_template("admin/create_share.html")

            existing = get_share_by_code(db, custom_code)
            if existing:
                flash("This code is already in use", "error")
                return render_template("admin/create_share.html")

            access_code = custom_code
        else:
            for _ in range(100):
                access_code = "".join(
                    secrets.choice(Config.CODE_CHARSET)
                    for _ in range(Config.SHARE_CODE_LENGTH)
                )
                existing = get_share_by_code(db, access_code)
                if not existing:
                    break
            else:
                flash("Failed to generate unique code. Try again.", "error")
                return render_template("admin/create_share.html")

        days = 0
        hours = 0
        try:
            if expires_days:
                days = int(expires_days)
            if expires_hours:
                hours = int(expires_hours)
        except (TypeError, ValueError):
            flash("Invalid expiration value", "error")
            return render_template("admin/create_share.html")

        if days < 0 or hours < 0 or hours > 23 or days > 365:
            flash("Invalid expiration range", "error")
            return render_template("admin/create_share.html")

        if days == 0 and hours == 0:
            days = 7
        total_seconds = days * 86400 + hours * 3600
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=total_seconds)).isoformat()

        limit = None
        if download_limit:
            try:
                limit = int(download_limit)
            except (TypeError, ValueError):
                flash("Invalid download limit", "error")
                return render_template("admin/create_share.html")
            if limit < 1 or limit > 1000000:
                flash("Download limit must be between 1 and 1000000", "error")
                return render_template("admin/create_share.html")

        viewlimit = None
        if view_limit:
            try:
                viewlimit = int(view_limit)
            except (TypeError, ValueError):
                flash("Invalid view limit", "error")
                return render_template("admin/create_share.html")
            if viewlimit < 1 or viewlimit > 1000000:
                flash("View limit must be between 1 and 1000000", "error")
                return render_template("admin/create_share.html")

        init_storage(Config.STORAGE_PATH)
        original_name = file.filename.strip()
        storage_name, file_size = save_file(Config.STORAGE_PATH, file, original_name)

        try:
            share_id = create_share(
                db, storage_name, original_name, file_size,
                access_code, expires_at, limit, viewlimit,
            )
        except sqlite3.IntegrityError:
            delete_file(Config.STORAGE_PATH, storage_name)
            flash("That code is already in use", "error")
            return render_template("admin/create_share.html")

        flash(f"Share created: {access_code}", "success")
        return redirect(url_for("admin.share_detail", share_id=share_id))

    return render_template("admin/create_share.html")


@admin_bp.route("/admin/shares/<int:share_id>")
@login_required
def share_detail(share_id):
    db = g.db
    share = get_share_by_id(db, share_id)
    if not share:
        abort(404)

    share["status"] = get_share_status(share)
    share_url = f"{Config.BASE_URL}/s/{share['access_code']}"

    activity = db.execute(
        """SELECT * FROM activity WHERE share_id = ?
           ORDER BY created_at DESC LIMIT 20""",
        (share_id,),
    ).fetchall()
    activity = [dict(r) for r in activity]

    return render_template("admin/share_detail.html",
        share=share, share_url=share_url, activity=activity,
    )


@admin_bp.route("/admin/shares/<int:share_id>/toggle", methods=["POST"])
@login_required
def toggle_share(share_id):
    db = g.db
    share = get_share_by_id(db, share_id)
    if not share:
        abort(404)

    new_state = 0 if share["enabled"] else 1
    update_share(db, share_id, enabled=new_state)
    action = "enabled" if new_state else "disabled"
    log_activity(db, share_id, f"share_{action}", f"Share {action} manually")
    flash(f"Share {action}", "success")
    return redirect(url_for("admin.share_detail", share_id=share_id))


@admin_bp.route("/admin/shares/<int:share_id>/delete", methods=["POST"])
@login_required
def delete_share_view(share_id):
    db = g.db
    share = get_share_by_id(db, share_id)
    if not share:
        abort(404)

    delete_file(Config.STORAGE_PATH, share["storage_name"])
    delete_share(db, share_id)
    flash("Share deleted", "success")
    return redirect(url_for("admin.shares_list"))


@admin_bp.route("/admin/activity")
@login_required
def activity_log():
    db = g.db
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    per_page = 50
    offset = (page - 1) * per_page
    activities = get_activity_log(db, limit=per_page, offset=offset)
    return render_template("admin/activity.html", activities=activities, page=page)
