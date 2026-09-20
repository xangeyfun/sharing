from conftest import login, create_share_via_form, _csrf_from, csrf_token
from app.config import Config
from app.database import get_db


def test_csrf_missing_token_rejected(app, client):
    resp = client.post("/admin/login", data={"username": "admin", "password": "x"})
    assert resp.status_code == 403


def test_create_share_via_form_and_download(app, client):
    login(client)
    resp = create_share_via_form(client, filename="photo.jpg", content=b"jpegdata",
                                 custom_code="myshare", expires_days="2", download_limit="3")
    body = resp.get_data(as_text=True)
    assert "myshare" in body

    resp = client.get("/s/myshare")
    assert resp.status_code == 200
    assert "photo.jpg" in resp.get_data(as_text=True)

    for _ in range(3):
        assert client.get("/s/myshare/download").status_code == 200
    assert client.get("/s/myshare/download").status_code == 404


def test_create_share_random_code_when_empty(app, client):
    login(client)
    resp = create_share_via_form(client, content=b"data", custom_code="")
    assert resp.status_code == 200
    with app.app_context():
        db = get_db(Config.DATABASE_PATH)
        row = db.execute("SELECT access_code FROM shares ORDER BY id DESC LIMIT 1").fetchone()
        assert row is not None
        assert len(row["access_code"]) == Config.SHARE_CODE_LENGTH


def test_custom_code_validation(app, client):
    login(client)

    resp = create_share_via_form(client, content=b"x", custom_code="../../etc")
    assert "letters, numbers, hyphens and underscores" in resp.get_data(as_text=True)

    resp = create_share_via_form(client, content=b"x", custom_code="ab!")
    assert "at least 4 characters" in resp.get_data(as_text=True)

    resp = create_share_via_form(client, content=b"x", custom_code="admin")
    assert "reserved" in resp.get_data(as_text=True)


def test_custom_code_collision(app, client):
    login(client)

    create_share_via_form(client, content=b"one", custom_code="unique1")
    resp = create_share_via_form(client, content=b"two", custom_code="unique1")
    assert "already in use" in resp.get_data(as_text=True)


def test_filenames_stored_outside_public_dir(app, client):
    login(client)
    create_share_via_form(client, filename="private.txt", content=b"secret")
    with app.app_context():
        db = get_db(Config.DATABASE_PATH)
        row = db.execute("SELECT * FROM shares ORDER BY id DESC LIMIT 1").fetchone()
        storage_name = row["storage_name"]
    import os
    stored = os.path.join(Config.STORAGE_PATH, storage_name)
    assert os.path.isfile(stored)
    assert "app/static" not in stored


def test_disable_and_reenable_share(app, client):
    login(client)
    create_share_via_form(client, content=b"data", custom_code="toggleme")
    assert client.get("/s/toggleme/download").status_code == 200

    with app.app_context():
        db = get_db(Config.DATABASE_PATH)
        share = db.execute("SELECT * FROM shares WHERE access_code = 'toggleme'").fetchone()
        share_id = share["id"]

    client.post(f"/admin/shares/{share_id}/toggle", data={"_csrf_token": csrf_token(client)})
    assert client.get("/s/toggleme/download").status_code == 404

    client.post(f"/admin/shares/{share_id}/toggle", data={"_csrf_token": csrf_token(client)})
    assert client.get("/s/toggleme/download").status_code == 200


def test_delete_share_removes_file(app, client):
    login(client)
    create_share_via_form(client, content=b"goner", custom_code="delete-me")
    assert client.get("/s/delete-me/download").status_code == 200

    with app.app_context():
        db = get_db(Config.DATABASE_PATH)
        share = db.execute("SELECT * FROM shares WHERE access_code = 'delete-me'").fetchone()
        share_id = share["id"]
        storage_name = share["storage_name"]

    import os
    file_path = os.path.join(Config.STORAGE_PATH, storage_name)

    client.post(f"/admin/shares/{share_id}/delete", data={"_csrf_token": csrf_token(client)})
    assert not os.path.exists(file_path)
    assert client.get("/s/delete-me/download").status_code == 404


def test_dashboard_stats(app, client):
    login(client)
    create_share_via_form(client, content=b"a", custom_code="stats1", expires_days="1", download_limit="1")
    create_share_via_form(client, content=b"b", custom_code="stats2", expires_days="1")
    client.get("/s/stats1/download")

    resp = client.get("/admin")
    body = resp.get_data(as_text=True)
    assert "Total Shares" in body
    assert "2" in body


def test_shares_list_shows_statuses(app, client):
    login(client)
    create_share_via_form(client, content=b"x", custom_code="listactv")
    with app.app_context():
        db = get_db(Config.DATABASE_PATH)
        db.execute("UPDATE shares SET expires_at = '2000-01-01T00:00:00+00:00' WHERE access_code = 'listactv'")
        db.commit()
    resp = client.get("/admin/shares")
    assert resp.status_code == 200
    assert "expired" in resp.get_data(as_text=True)


def test_activity_page(app, client):
    login(client)
    create_share_via_form(client, content=b"x", custom_code="actime")
    client.get("/s/actime/download")
    resp = client.get("/admin/activity")
    body = resp.get_data(as_text=True)
    assert "share_created" in body
    assert "share_accessed" in body


def test_ratelimited_login_returns_redirect(app, client):
    resp = client.get("/admin/login")
    token = _csrf_from(resp.get_data(as_text=True))
    for _ in range(6):
        client.post("/admin/login", data={
            "username": "admin", "password": "wrong", "_csrf_token": token,
        })
    resp = client.post("/admin/login", data={
        "username": "admin", "password": "wrong", "_csrf_token": token,
    }, follow_redirects=True)
    assert "Too many attempts" in resp.get_data(as_text=True)