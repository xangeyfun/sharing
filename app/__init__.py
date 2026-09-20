import os
import logging
from datetime import datetime, timezone, timedelta

from flask import (
    Flask, redirect, url_for, request,
    g, jsonify, render_template, flash
)
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import Config
from app.database import init_db, get_db, close_db


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(Config)
    app.config["SESSION_COOKIE_NAME"] = "share_session"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = Config.SECURE_COOKIES
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(seconds=Config.SESSION_LIFETIME)
    app.config["MAX_CONTENT_LENGTH"] = Config.MAX_UPLOAD_SIZE
    app.config["TESTING"] = Config.TESTING

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    errors = Config().validate()
    if errors:
        for err in errors:
            logging.warning("Configuration problem: %s", err)

    os.makedirs(os.path.dirname(Config.DATABASE_PATH) or ".", exist_ok=True)
    os.makedirs(Config.STORAGE_PATH, exist_ok=True)
    init_db(Config.DATABASE_PATH)

    from app.csrf import csrf_init
    csrf_init(app)

    from app.maintenance import start_maintenance_thread
    start_maintenance_thread(app)

    @app.before_request
    def load_db():
        g.db = get_db(Config.DATABASE_PATH)

    @app.teardown_appcontext
    def shutdown_db(exc):
        close_db(Config.DATABASE_PATH)

    from app.blueprints.admin import admin_bp
    from app.blueprints.share import share_bp
    from app.blueprints.api import api_bp

    app.register_blueprint(admin_bp)
    app.register_blueprint(share_bp)
    app.register_blueprint(api_bp)

    @app.context_processor
    def inject_globals():
        return {
            "base_url": Config.BASE_URL,
            "now": datetime.now(timezone.utc),
        }

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.path.startswith("/admin"):
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "img-src 'self' https://xangey.dev data:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'",
        )
        resp.headers.setdefault("Permissions-Policy", "interest-cohort=()")
        return resp

    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith("/api/"):
            return jsonify(error="Not found"), 404
        if request.path.startswith("/s/"):
            return render_template("errors/404.html"), 404
        return redirect(url_for("admin.login"))

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(413)
    def too_large(e):
        if request.path.startswith("/api/"):
            return jsonify(error="File too large"), 413
        flash("File too large", "error")
        return redirect(url_for("admin.create_share_view"))

    @app.errorhandler(500)
    def server_error(e):
        if request.path.startswith("/api/"):
            return jsonify(error="Internal server error"), 500
        return render_template("errors/500.html"), 500

    return app