import time
import threading
from collections import defaultdict, deque
from functools import wraps

from flask import request, jsonify, redirect, url_for, flash, session


class RateLimiter:
    def __init__(self, max_requests, window_seconds):
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, scope, identity=None):
        key = ":".join([scope, identity or (request.remote_addr or "unknown")])
        now = time.monotonic()
        with self._lock:
            dq = self._hits[key]
            while dq and dq[0] < now - self.window:
                dq.popleft()
            if len(dq) >= self.max_requests:
                dq.append(now)
                return False
            dq.append(now)
            return True


_login_limiter = RateLimiter(5, 300)
_2fa_limiter = RateLimiter(5, 300)
_api_limiter = RateLimiter(120, 60)
_upload_limiter = RateLimiter(30, 3600)
_media_limiter = RateLimiter(600, 60)


def _only_state_changing():
    return request.method in ("POST", "PUT", "PATCH", "DELETE")


def rate_limit_auth(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if _only_state_changing() and not _login_limiter.hit("login", request.remote_addr):
            flash("Too many attempts. Try again later.", "error")
            return redirect(url_for("admin.login"))
        return f(*args, **kwargs)
    return wrapped


def rate_limit_2fa(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if _only_state_changing() and not _2fa_limiter.hit("2fa", request.remote_addr):
            flash("Too many attempts. Try again later.", "error")
            session.clear()
            return redirect(url_for("admin.login"))
        return f(*args, **kwargs)
    return wrapped


def rate_limit_api(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not _api_limiter.hit("api", request.remote_addr):
            return jsonify(error="Too many requests"), 429
        return f(*args, **kwargs)
    return wrapped


def rate_limit_upload(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if request.method == "POST" and not _upload_limiter.hit("upload", request.remote_addr):
            flash("Too many uploads. Try again later.", "error")
            return redirect(url_for("admin.create_share_view"))
        return f(*args, **kwargs)
    return wrapped


def rate_limit_media(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not _media_limiter.hit("media", request.remote_addr):
            return jsonify(error="Too many requests"), 429
        return f(*args, **kwargs)
    return wrapped


def reset_limits():
    for limiter in (_login_limiter, _2fa_limiter, _api_limiter, _upload_limiter, _media_limiter):
        with limiter._lock:
            limiter._hits.clear()