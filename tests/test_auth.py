from datetime import datetime, timezone, timedelta

from conftest import login, TEST_PASSWORD, _csrf_from
from app.database import get_db, create_twofa_code, verify_twofa_code, consume_twofa_code
from app.config import Config


def test_full_login_flow(app, client):
    login(client)
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert "Overview" in resp.get_data(as_text=True)


def test_wrong_password_rejected(app, client):

    resp = client.get("/admin/login")
    token = _csrf_from(resp.get_data(as_text=True))
    resp = client.post("/admin/login", data={
        "username": "admin",
        "password": "wrong-password",
        "_csrf_token": token,
    }, follow_redirects=True)
    assert "Invalid credentials" in resp.get_data(as_text=True)
    resp = client.get("/admin")
    assert resp.status_code == 302


def test_wrong_username_rejected(app, client):

    resp = client.get("/admin/login")
    token = _csrf_from(resp.get_data(as_text=True))
    resp = client.post("/admin/login", data={
        "username": "imposter",
        "password": TEST_PASSWORD,
        "_csrf_token": token,
    }, follow_redirects=True)
    assert "Invalid credentials" in resp.get_data(as_text=True)


def test_wrong_2fa_code_rejected(app, client):

    resp = client.get("/admin/login")
    token = _csrf_from(resp.get_data(as_text=True))
    client.post("/admin/login", data={
        "username": "admin",
        "password": TEST_PASSWORD,
        "_csrf_token": token,
    })

    resp = client.get("/admin/verify-2fa")
    token = _csrf_from(resp.get_data(as_text=True))
    resp = client.post("/admin/verify-2fa", data={
        "code": "ZZZZZZ",
        "_csrf_token": token,
    }, follow_redirects=True)
    assert "Incorrect code" in resp.get_data(as_text=True)
    assert client.get("/admin").status_code == 302


def test_2fa_code_expired(app):
    with app.app_context():
        db = get_db(Config.DATABASE_PATH)
        from argon2 import PasswordHasher
        ph = PasswordHasher()
        code_hash = ph.hash("ABC123")
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        code_id = create_twofa_code(db, code_hash, expired)
        assert verify_twofa_code(db, code_id) is False
        assert verify_twofa_code(db, code_id) is False


def test_2fa_code_single_use(app):
    with app.app_context():
        db = get_db(Config.DATABASE_PATH)
        from argon2 import PasswordHasher
        ph = PasswordHasher()
        code_hash = ph.hash("ABC123")
        code_id = create_twofa_code(
            db, code_hash,
            (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        )
        assert verify_twofa_code(db, code_id) is True
        consume_twofa_code(db, code_id)
        assert verify_twofa_code(db, code_id) is False


def test_2fa_attempt_limit_invalidates_code(app):
    with app.app_context():
        db = get_db(Config.DATABASE_PATH)
        from argon2 import PasswordHasher
        ph = PasswordHasher()
        code_hash = ph.hash("ABC123")
        code_id = create_twofa_code(
            db, code_hash,
            (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        )
        for _ in range(Config.TWO_FA_MAX_ATTEMPTS):
            assert verify_twofa_code(db, code_id) is True
        assert verify_twofa_code(db, code_id) is False


def test_admin_routes_require_auth(app, client):
    for path in ["/admin", "/admin/shares", "/admin/shares/create", "/admin/activity"]:
        resp = client.get(path)
        assert resp.status_code == 302
        assert "/admin/login" in resp.headers["Location"]


def test_logout_clears_session(app, client):
    login(client)
    token = _csrf_from(client.get("/admin/shares/create").get_data(as_text=True))
    resp = client.post("/admin/logout", data={"_csrf_token": token})
    assert resp.status_code == 302
    assert client.get("/admin").status_code == 302


def test_login_requires_both_fields(app, client):

    resp = client.get("/admin/login")
    token = _csrf_from(resp.get_data(as_text=True))
    resp = client.post("/admin/login", data={"password": TEST_PASSWORD, "_csrf_token": token},
                       follow_redirects=True)
    assert "Username and password required" in resp.get_data(as_text=True)