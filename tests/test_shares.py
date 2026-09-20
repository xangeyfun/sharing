import concurrent.futures

from app.config import Config
from app.database import get_db


def test_public_info_page_shows_file_details(app, client, make_share):
    share = make_share(filename="hello.txt", content=b"hello", download_limit=5)
    resp = client.get(f"/s/{share['access_code']}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "hello.txt" in body
    assert "5" in body
    assert "Download" in body


def test_download_succeeds_and_content_matches(app, client, make_share):
    share = make_share(filename="repo.zip", content=b"binary\x00data\xff")
    resp = client.get(f"/s/{share['access_code']}/download")
    assert resp.status_code == 200
    assert resp.data == b"binary\x00data\xff"
    assert "attachment" in resp.headers["Content-Disposition"]


def test_binary_content_does_not_leak_into_info_page(app, client, make_share):
    share = make_share(filename="auto.bin", content=b"should-not-appear-in-info")
    resp = client.get(f"/s/{share['access_code']}")
    assert resp.status_code == 200
    assert "should-not-appear-in-info" not in resp.get_data(as_text=True)


def test_download_increments_download_count(app, client, make_share):
    share = make_share(filename="counted.txt", content=b"data", download_limit=3)
    for i in range(3):
        resp = client.get(f"/s/{share['access_code']}/download")
        assert resp.status_code == 200
    resp = client.get(f"/s/{share['access_code']}/download")
    assert resp.status_code == 404

    with app.app_context():
        db = get_db(Config.DATABASE_PATH)
        row = db.execute("SELECT download_count FROM shares WHERE id = ?", (share["id"],)).fetchone()
        assert row["download_count"] == 3


def test_expired_share_rejects_download(app, client, make_share):
    share = make_share(filename="old.txt", content=b"data", expires_days=-1)
    resp = client.get(f"/s/{share['access_code']}")
    assert resp.status_code == 404
    resp = client.get(f"/s/{share['access_code']}/download")
    assert resp.status_code == 404


def test_invalid_and_nonexistent_codes_return_404(app, client):
    resp = client.get("/s/definitelynotarealcodexyz")
    assert resp.status_code == 404

    resp = client.get("/s/")
    assert resp.status_code == 404


def test_disabled_share_rejects_download(app, client, make_share):
    share = make_share(filename="disabled.txt", content=b"data", enabled=0)
    resp = client.get(f"/s/{share['access_code']}")
    assert resp.status_code == 404
    resp = client.get(f"/s/{share['access_code']}/download")
    assert resp.status_code == 404


def test_deleted_share_rejects_download(app, client, make_share):
    share = make_share(filename="goner.txt", content=b"data")
    with app.app_context():
        db = get_db(Config.DATABASE_PATH)
        db.execute("UPDATE shares SET deleted = 1 WHERE id = ?", (share["id"],))
        db.commit()
    resp = client.get(f"/s/{share['access_code']}")
    assert resp.status_code == 404
    resp = client.get(f"/s/{share['access_code']}/download")
    assert resp.status_code == 404


def test_concurrent_downloads_with_limit_one_app_only_once(app, client, make_share):
    share = make_share(filename="race.txt", content=b"race", download_limit=1)
    url = f"/s/{share['access_code']}/download"

    def fetch():
        c = app.test_client()
        return c.get(url, buffered=True).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch) for _ in range(8)]
        results = [f.result() for f in futures]

    assert results.count(200) == 1
    assert results.count(404) == 7


def test_large_file_streaming(app, client, make_share):
    content = b"a" * (8 * 1024 * 1024)
    share = make_share(filename="large.bin", content=content)
    resp = client.get(f"/s/{share['access_code']}/download")
    assert resp.status_code == 200
    assert resp.data == content
    assert len(resp.data) == 8 * 1024 * 1024


def test_missing_file_on_disk_is_unavailable(app, client, make_share):
    share = make_share(filename="missing.bin", content=b"data")
    with app.app_context():
        db = get_db(Config.DATABASE_PATH)
        db.execute("UPDATE shares SET storage_name = 'doesnotexist123' WHERE id = ?", (share["id"],))
        db.commit()
    resp = client.get(f"/s/{share['access_code']}/download")
    assert resp.status_code == 404


def test_get_file_path_rejects_traversal(tmp_path, app):
    from app.storage import get_file_path
    assert get_file_path(Config.STORAGE_PATH, "../../etc/passwd") is None
    assert get_file_path(Config.STORAGE_PATH, "..") is None
    assert get_file_path(Config.STORAGE_PATH, "notpresentfile") is None


def test_upload_filename_validation_rejects_traversal(app):
    from app.storage import validate_upload_filename
    ok, _ = validate_upload_filename("../../etc/passwd")
    assert not ok
    ok, _ = validate_upload_filename("sub/evil.txt")
    assert not ok
    ok, _ = validate_upload_filename("..")
    assert not ok
    ok, _ = validate_upload_filename("normal file.txt")
    assert ok


def test_content_disposition_encoding(app):
    from app.storage import content_disposition
    hdr = content_disposition('report "final".pdf')
    assert "filename*=" in hdr
    assert "UTF-8''" in hdr


def test_api_status_endpoint(app, client, make_share):
    share = make_share(content=b"data")
    resp = client.get(f"/api/share/{share['access_code']}/status")
    assert resp.status_code == 200
    assert resp.get_json()["valid"] is True

    resp = client.get("/api/share/missingcode/status")
    assert resp.get_json()["valid"] is False


def test_public_page_shows_remaining_downloads(app, client, make_share):
    share = make_share(content=b"data", download_limit=2)
    with app.app_context():
        db = get_db(Config.DATABASE_PATH)
        db.execute("UPDATE shares SET download_count = 1 WHERE id = ?", (share["id"],))
        db.commit()
    resp = client.get(f"/s/{share['access_code']}")
    body = resp.get_data(as_text=True)
    assert "1" in body
    assert "Remaining downloads" in body


def test_expired_share_still_listed_but_details_valid_states(app, client, make_share):
    share = make_share(filename="still-listed.txt", content=b"x", expires_days=-1)
    from app.database import get_share_status
    with app.app_context():
        db = get_db(Config.DATABASE_PATH)
        row = db.execute("SELECT * FROM shares WHERE id = ?", (share["id"],)).fetchone()
        assert get_share_status(dict(row)) == "expired"


def test_text_file_shows_escaped_preview(app, client, make_share):
    share = make_share(filename="notes.txt", content=b"<script>alert(1)</script>\nhello world")
    resp = client.get(f"/s/{share['access_code']}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "hello world" in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "<script>alert(1)</script>" not in body


def test_large_text_preview_is_truncated(app, client, make_share):
    content = b"A" * (200 * 1024)
    share = make_share(filename="big.txt", content=content)
    body = client.get(f"/s/{share['access_code']}").get_data(as_text=True)
    assert "Preview cut off at 64 KB" in body


def test_image_share_embeds_preview_and_serves_inline(app, client, make_share):
    png = b"\x89PNG\r\n\x1a\n" + b"imagedata"
    share = make_share(filename="pic.png", content=png)
    page = client.get(f"/s/{share['access_code']}")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert f"/s/{share['access_code']}/preview" in body
    assert "<img" in body
    assert "<video" not in body

    resp = client.get(f"/s/{share['access_code']}/preview")
    assert resp.status_code == 200
    assert resp.data == png
    assert resp.headers["Content-Type"] == "image/png"
    assert "inline" in resp.headers["Content-Disposition"]


def test_audio_share_embeds_audio_player(app, client, make_share):
    share = make_share(filename="tune.mp3", content=b"ID3audio")
    body = client.get(f"/s/{share['access_code']}").get_data(as_text=True)
    assert "<audio" in body
    resp = client.get(f"/s/{share['access_code']}/preview")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "audio/mpeg"
    assert resp.data == b"ID3audio"


def test_pdf_share_embeds_iframe(app, client, make_share):
    share = make_share(filename="doc.pdf", content=b"%PDF-1.4 example")
    body = client.get(f"/s/{share['access_code']}").get_data(as_text=True)
    assert "<iframe" in body
    resp = client.get(f"/s/{share['access_code']}/preview")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/pdf"


def test_video_preview_supports_range_requests(app, client, make_share):
    content = b"f" * 100000 + b"m" * 100000
    share = make_share(filename="movie.mp4", content=content)
    url = f"/s/{share['access_code']}/preview"

    resp = client.get(url, headers={"Range": "bytes=0-99"})
    assert resp.status_code == 206
    assert resp.headers["Content-Range"] == f"bytes 0-99/{len(content)}"
    assert int(resp.headers["Content-Length"]) == 100
    assert resp.data == content[:100]

    resp = client.get(url, headers={"Range": "bytes=500-"})
    assert resp.status_code == 206
    assert resp.headers["Content-Range"] == f"bytes 500-{len(content) - 1}/{len(content)}"
    assert resp.data == content[500:]

    resp = client.get(url, headers={"Range": "bytes=-20"})
    assert resp.status_code == 206
    assert resp.headers["Content-Range"] == f"bytes {len(content) - 20}-{len(content) - 1}/{len(content)}"
    assert resp.data == content[-20:]

    resp = client.get(url, headers={"Range": "bytes=99999999999999-"})
    assert resp.status_code == 416
    assert resp.headers["Content-Range"] == f"bytes */{len(content)}"

    resp = client.get(url)
    assert resp.status_code == 200
    assert int(resp.headers["Content-Length"]) == len(content)


def test_preview_does_not_consume_download_limit(app, client, make_share):
    share = make_share(filename="photo.jpg", content=b"\xff\xd8\xffimag", download_limit=1)
    client.get(f"/s/{share['access_code']}/preview")
    client.get(f"/s/{share['access_code']}/preview")
    page = client.get(f"/s/{share['access_code']}")
    assert page.status_code == 200
    resp = client.get(f"/s/{share['access_code']}/download")
    assert resp.status_code == 200
    resp = client.get(f"/s/{share['access_code']}/download")
    assert resp.status_code == 404


def test_preview_allowed_after_download_exhaustion(app, client, make_share):
    share = make_share(filename="m.jpg", content=b"x", download_limit=1)
    client.get(f"/s/{share['access_code']}/download")
    resp = client.get(f"/s/{share['access_code']}/preview")
    assert resp.status_code == 200


def test_preview_rejected_after_expiry_and_disabling(app, client, make_share):
    share = make_share(filename="e.png", content=b"x", expires_days=-1)
    resp = client.get(f"/s/{share['access_code']}/preview")
    assert resp.status_code == 404

    share2 = make_share(filename="d.png", content=b"x", enabled=0)
    resp = client.get(f"/s/{share2['access_code']}/preview")
    assert resp.status_code == 404


def test_binary_file_has_no_preview(app, client, make_share):
    share = make_share(filename="archive.zip", content=b"PK\x03\x04data")
    body = client.get(f"/s/{share['access_code']}").get_data(as_text=True)
    assert "/preview" not in body
    assert ">Preview<" not in body


def test_view_limit_counts_page_views(app, client, make_share):
    share = make_share(filename="views.txt", content=b"data", view_limit=2)
    for _ in range(2):
        assert client.get(f"/s/{share['access_code']}").status_code == 200
    assert client.get(f"/s/{share['access_code']}").status_code == 404

    with app.app_context():
        db = get_db(Config.DATABASE_PATH)
        row = db.execute("SELECT view_count FROM shares WHERE id = ?", (share["id"],)).fetchone()
        assert row["view_count"] == 2


def test_page_view_does_not_increment_download_count(app, client, make_share):
    share = make_share(filename="d.txt", content=b"data")
    client.get(f"/s/{share['access_code']}")
    with app.app_context():
        db = get_db(Config.DATABASE_PATH)
        row = db.execute("SELECT download_count FROM shares WHERE id = ?", (share["id"],)).fetchone()
        assert row["download_count"] == 0


def test_download_still_works_after_view_limit_reached(app, client, make_share):
    share = make_share(filename="v.txt", content=b"data", view_limit=1)
    assert client.get(f"/s/{share['access_code']}").status_code == 200
    resp = client.get(f"/s/{share['access_code']}/download")
    assert resp.status_code == 200
    assert resp.data == b"data"


def test_page_still_viewable_after_download_limit_reached(app, client, make_share):
    share = make_share(filename="w.txt", content=b"data", download_limit=1)
    assert client.get(f"/s/{share['access_code']}/download").status_code == 200
    resp = client.get(f"/s/{share['access_code']}")
    assert resp.status_code == 200
    assert "w.txt" in resp.get_data(as_text=True)


def test_concurrent_page_views_with_limit_one_app_only_once(app, client, make_share):
    share = make_share(filename="vrace.txt", content=b"race", view_limit=1)
    url = f"/s/{share['access_code']}"

    def fetch():
        c = app.test_client()
        return c.get(url).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch) for _ in range(8)]
        results = [f.result() for f in futures]

    assert results.count(200) == 1
    assert results.count(404) == 7


def test_migrates_old_access_schema(tmp_path, app):
    import sqlite3
    from app.database import init_db

    old_db = tmp_path / "old.db"
    conn = sqlite3.connect(old_db)
    conn.executescript("""
        CREATE TABLE shares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            storage_name TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            access_code TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            access_limit INTEGER,
            access_count INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            deleted INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO shares (storage_name, original_filename, file_size, access_code,
                            created_at, expires_at, access_limit, access_count)
        VALUES ('f1', 'old.txt', 5, 'oldcode1', '2026-01-01T00:00:00+00:00',
                '2026-12-31T00:00:00+00:00', 3, 2);
    """)
    conn.commit()
    conn.close()

    init_db(str(old_db))
    conn = sqlite3.connect(old_db)
    conn.row_factory = sqlite3.Row
    cols = {row[1] for row in conn.execute("PRAGMA table_info(shares)")}
    assert "access_limit" not in cols
    assert "access_count" not in cols
    assert "download_limit" in cols
    assert "download_count" in cols
    assert "view_limit" in cols
    assert "view_count" in cols
    row = conn.execute("SELECT * FROM shares WHERE access_code = 'oldcode1'").fetchone()
    assert row["download_limit"] == 3
    assert row["download_count"] == 2
    assert row["view_count"] == 0
    conn.close()