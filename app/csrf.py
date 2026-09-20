import secrets
import hmac
from functools import wraps
from flask import session, request, abort


def generate_csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)
    return session["_csrf_token"]


def validate_csrf_token():
    token = session.get("_csrf_token")
    form_token = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
    if not token or not form_token:
        return False
    return hmac.compare_digest(token, form_token)


def csrf_init(app):
    app.jinja_env.globals["csrf_token"] = generate_csrf_token

    @app.before_request
    def csrf_protect():
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return
        if request.path.startswith("/static/"):
            return
        if not validate_csrf_token():
            abort(403)


def csrf_protected(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not validate_csrf_token():
            abort(403)
        return f(*args, **kwargs)
    return decorated
