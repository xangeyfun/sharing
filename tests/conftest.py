import io
import os
import re
import tempfile

import pytest

TEST_DIR = tempfile.mkdtemp(prefix="share-tests-")

os.environ["DATABASE_PATH"] = os.path.join(TEST_DIR, "test.db")
os.environ["STORAGE_PATH"] = os.path.join(TEST_DIR, "files")
os.environ["SECRET_KEY"] = "x" * 48
os.environ["SECURE_COOKIES"] = "0"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD_HASH"] = (
    "$argon2id$v=19$m=65536,t=3,p=4$+Y/5jG00HnK4JuRSofPgCA$"
    "2Y3+uSXAMQuqUmOPtGVw1EYfSp38zTycgc5toT85dm8"
)
os.environ["DISCORD_WEBHOOK_URL"] = "https://example.invalid/webhook"
os.environ["TESTING"] = "1"
os.environ["BASE_URL"] = "https://share.xangey.dev"

from app import create_app  # noqa: E402
from app.config import Config  # noqa: E402
from app.database import init_db, create_share, get_db  # noqa: E402
from app.storage import save_file, init_storage  # noqa: E402
from app.ratelimit import reset_limits  # noqa: E402
from werkzeug.datastructures import FileStorage  # noqa: E402

TEST_PASSWORD = "test-password"


def _csrf_from(html):
    match = re.search(r'name="_csrf_token" value="([^"]+)"', html)
    assert match, "CSRF token not found in response"
    return match.group(1)


@pytest.fixture()
def app(monkeypatch):
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(Config.DATABASE_PATH + suffix):
            os.remove(Config.DATABASE_PATH + suffix)
    init_db(Config.DATABASE_PATH)
    init_storage(Config.STORAGE_PATH)
    for name in os.listdir(Config.STORAGE_PATH):
        os.remove(os.path.join(Config.STORAGE_PATH, name))

    reset_limits()
    test_app = create_app()
    test_app.config["MAX_CONTENT_LENGTH"] = Config.MAX_UPLOAD_SIZE

    monkeypatch.setattr("app.blueprints.admin.generate_2fa_code", lambda: "ABC123")
    monkeypatch.setattr("app.blueprints.admin.send_2fa_discord", lambda code: True)

    yield test_app


@pytest.fixture()
def client(app):
    return app.test_client()


_share_counter = [0]


@pytest.fixture()
def make_share(app):
    def _make(filename="test.txt", content=b"hello world", download_limit=None,
              view_limit=None, expires_days=7, expires_hours=0, code=None, enabled=1):
        if code is None:
            _share_counter[0] += 1
            code = f"testcode{_share_counter[0]:05d}"
        with app.app_context():
            storage_name, size = save_file(
                Config.STORAGE_PATH,
                FileStorage(stream=io.BytesIO(content), filename=filename),
                filename,
            )
            share_id = create_share(
                get_db(Config.DATABASE_PATH), storage_name, filename, size, code,
                iso_from_now(days=expires_days, hours=expires_hours),
                download_limit, view_limit,
            )
            db = get_db(Config.DATABASE_PATH)
            if not enabled:
                db.execute("UPDATE shares SET enabled = 0 WHERE id = ?", (share_id,))
                db.commit()
            row = db.execute("SELECT * FROM shares WHERE id = ?", (share_id,)).fetchone()
            return dict(row)
    return _make


def iso_from_now(days=0, hours=0):
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc) + timedelta(days=days, hours=hours)).isoformat()


def login(client, username="admin", password=TEST_PASSWORD):
    resp = client.get("/admin/login")
    token = _csrf_from(resp.get_data(as_text=True))
    resp = client.post("/admin/login", data={
        "username": username,
        "password": password,
        "_csrf_token": token,
    }, follow_redirects=False)
    assert resp.status_code == 302
    assert "/admin/verify-2fa" in resp.headers["Location"]

    resp = client.get("/admin/verify-2fa")
    token = _csrf_from(resp.get_data(as_text=True))
    resp = client.post("/admin/verify-2fa", data={
        "code": "ABC123",
        "_csrf_token": token,
    }, follow_redirects=False)
    assert resp.status_code == 302
    assert "/admin" in resp.headers["Location"]
    return resp


def csrf_token(client):
    resp = client.get("/admin/shares/create")
    if resp.status_code != 200:
        return None
    return _csrf_from(resp.get_data(as_text=True))


def create_share_via_form(client, filename="upload.bin", content=b"data",
                          custom_code="", expires_days="1", download_limit="", view_limit=""):
    resp = client.get("/admin/shares/create")
    token = _csrf_from(resp.get_data(as_text=True))
    resp = client.post("/admin/shares/create", data={
        "file": (io.BytesIO(content), filename),
        "custom_code": custom_code,
        "expires_days": expires_days,
        "expires_hours": "",
        "download_limit": download_limit,
        "view_limit": view_limit,
        "_csrf_token": token,
    }, content_type="multipart/form-data", follow_redirects=True)
    return resp