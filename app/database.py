import sqlite3
import os
import threading
from datetime import datetime, timezone


_local = threading.local()


def get_db(db_path):
    if not hasattr(_local, "connections"):
        _local.connections = {}
    if db_path not in _local.connections:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        _local.connections[db_path] = conn
    return _local.connections[db_path]


def close_db(db_path):
    if hasattr(_local, "connections") and db_path in _local.connections:
        _local.connections[db_path].close()
        del _local.connections[db_path]


def init_db(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(shares)")}
    if "access_limit" in cols:
        conn.execute("ALTER TABLE shares RENAME COLUMN access_limit TO download_limit")
        conn.execute("ALTER TABLE shares RENAME COLUMN access_count TO download_count")
        cols.discard("access_limit")
        cols.discard("access_count")
        cols.update({"download_limit", "download_count"})
    if "expiry_notified" not in cols:
        conn.execute("ALTER TABLE shares ADD COLUMN expiry_notified INTEGER NOT NULL DEFAULT 0")
    if "view_limit" not in cols:
        conn.execute("ALTER TABLE shares ADD COLUMN view_limit INTEGER")
    if "view_count" not in cols:
        conn.execute("ALTER TABLE shares ADD COLUMN view_count INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS shares (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    storage_name TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    access_code TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    download_limit INTEGER,
    download_count INTEGER NOT NULL DEFAULT 0,
    view_limit INTEGER,
    view_count INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    deleted INTEGER NOT NULL DEFAULT 0,
    expiry_notified INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_shares_access_code ON shares(access_code);
CREATE INDEX IF NOT EXISTS idx_shares_enabled ON shares(enabled);
CREATE INDEX IF NOT EXISTS idx_shares_deleted ON shares(deleted);
CREATE INDEX IF NOT EXISTS idx_shares_expires_at ON shares(expires_at);

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    share_id INTEGER,
    event_type TEXT NOT NULL,
    event_detail TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (share_id) REFERENCES shares(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_activity_share_id ON activity(share_id);
CREATE INDEX IF NOT EXISTS idx_activity_created_at ON activity(created_at);
CREATE INDEX IF NOT EXISTS idx_activity_event_type ON activity(event_type);

CREATE TABLE IF NOT EXISTS twofa_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_twofa_codes_expires_at ON twofa_codes(expires_at);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def log_activity(db, share_id, event_type, event_detail=None):
    db.execute(
        "INSERT INTO activity (share_id, event_type, event_detail, created_at) VALUES (?, ?, ?, ?)",
        (share_id, event_type, event_detail, now_iso()),
    )
    db.commit()


def create_share(db, storage_name, original_filename, file_size, access_code,
                 expires_at, download_limit=None, view_limit=None):
    created = now_iso()
    cursor = db.execute(
        """INSERT INTO shares
           (storage_name, original_filename, file_size, access_code, created_at,
            expires_at, download_limit, download_count, view_limit, view_count,
            enabled, deleted)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, 0, 1, 0)""",
        (storage_name, original_filename, file_size, access_code, created,
         expires_at, download_limit, view_limit),
    )
    db.commit()
    share_id = cursor.lastrowid
    log_activity(db, share_id, "share_created", f"Created share for {original_filename}")
    return share_id


def get_share_by_code(db, access_code):
    row = db.execute(
        "SELECT * FROM shares WHERE access_code = ? AND deleted = 0",
        (access_code,),
    ).fetchone()
    return dict(row) if row else None


def get_share_by_id(db, share_id):
    row = db.execute(
        "SELECT * FROM shares WHERE id = ? AND deleted = 0",
        (share_id,),
    ).fetchone()
    return dict(row) if row else None


def try_download_share(db, share_id):
    """Atomically check validity and increment the download count. Returns True if granted."""
    now = now_iso()
    cursor = db.execute(
        """UPDATE shares
           SET download_count = download_count + 1
           WHERE id = ?
             AND deleted = 0
             AND enabled = 1
             AND expires_at > ?
             AND (download_limit IS NULL OR download_count < download_limit)""",
        (share_id, now),
    )
    db.commit()
    return cursor.rowcount > 0


def try_view_share(db, share_id):
    """Atomically check validity and increment the page-view count. Returns True if granted."""
    now = now_iso()
    cursor = db.execute(
        """UPDATE shares
           SET view_count = view_count + 1
           WHERE id = ?
             AND deleted = 0
             AND enabled = 1
             AND expires_at > ?
             AND (view_limit IS NULL OR view_count < view_limit)""",
        (share_id, now),
    )
    db.commit()
    return cursor.rowcount > 0


def _limits_exhausted(share):
    if share["download_limit"] is not None and share["download_count"] >= share["download_limit"]:
        return True
    if share["view_limit"] is not None and share["view_count"] >= share["view_limit"]:
        return True
    return False


def is_share_valid(share):
    now = datetime.now(timezone.utc)
    expires = datetime.fromisoformat(share["expires_at"])
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if not share["enabled"]:
        return False
    if now >= expires:
        return False
    if _limits_exhausted(share):
        return False
    return True


def get_share_status(share):
    now = datetime.now(timezone.utc)
    expires = datetime.fromisoformat(share["expires_at"])
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if share["deleted"]:
        return "deleted"
    if not share["enabled"]:
        return "disabled"
    if now >= expires:
        return "expired"
    if _limits_exhausted(share):
        return "exhausted"
    return "active"


def list_shares(db, status_filter=None, search=None, sort_by="created_at", sort_dir="DESC", limit=100, offset=0):
    conditions = ["deleted = 0"]
    params = []

    if status_filter == "active":
        conditions.append("enabled = 1")
        conditions.append("expires_at > ?")
        params.append(now_iso())
        conditions.append("(download_limit IS NULL OR download_count < download_limit)")
        conditions.append("(view_limit IS NULL OR view_count < view_limit)")
    elif status_filter == "expired":
        conditions.append("expires_at <= ?")
        params.append(now_iso())
    elif status_filter == "exhausted":
        conditions.append("enabled = 1")
        conditions.append("expires_at > ?")
        params.append(now_iso())
        conditions.append("((download_limit IS NOT NULL AND download_count >= download_limit) OR (view_limit IS NOT NULL AND view_count >= view_limit))")
    elif status_filter == "disabled":
        conditions.append("enabled = 0")

    if search:
        conditions.append("(original_filename LIKE ? OR access_code LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    allowed_sorts = {"created_at", "expires_at", "file_size", "download_count", "view_count", "original_filename", "access_code"}
    if sort_by not in allowed_sorts:
        sort_by = "created_at"
    sort_dir = "ASC" if sort_dir.upper() == "ASC" else "DESC"

    where = " AND ".join(conditions)
    query = f"SELECT * FROM shares WHERE {where} ORDER BY {sort_by} {sort_dir} LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = db.execute(query, params).fetchall()

    count_query = f"SELECT COUNT(*) FROM shares WHERE {where}"
    count_params = params[:-2]
    total = db.execute(count_query, count_params).fetchone()[0]

    return [dict(r) for r in rows], total


def update_share(db, share_id, **kwargs):
    allowed = {"enabled", "download_limit", "view_limit", "expires_at", "access_code"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [share_id]
    db.execute(f"UPDATE shares SET {set_clause} WHERE id = ?", values)
    db.commit()


def delete_share(db, share_id):
    share = get_share_by_id(db, share_id)
    if share:
        db.execute("UPDATE shares SET deleted = 1 WHERE id = ?", (share_id,))
        log_activity(db, share_id, "share_deleted", f"Deleted share: {share['original_filename']}")
        db.commit()
    return share


def get_stats(db):
    now = now_iso()
    total = db.execute("SELECT COUNT(*) FROM shares WHERE deleted = 0").fetchone()[0]
    active = db.execute(
        """SELECT COUNT(*) FROM shares
           WHERE deleted = 0 AND enabled = 1 AND expires_at > ?
             AND (download_limit IS NULL OR download_count < download_limit)
             AND (view_limit IS NULL OR view_count < view_limit)""",
        (now,),
    ).fetchone()[0]
    expired = db.execute(
        "SELECT COUNT(*) FROM shares WHERE deleted = 0 AND expires_at <= ?",
        (now,),
    ).fetchone()[0]
    exhausted = db.execute(
        """SELECT COUNT(*) FROM shares
           WHERE deleted = 0 AND enabled = 1 AND expires_at > ?
             AND ((download_limit IS NOT NULL AND download_count >= download_limit) OR (view_limit IS NOT NULL AND view_count >= view_limit))""",
        (now,),
    ).fetchone()[0]
    disabled = db.execute(
        "SELECT COUNT(*) FROM shares WHERE deleted = 0 AND enabled = 0",
    ).fetchone()[0]
    total_downloads = db.execute(
        "SELECT COALESCE(SUM(download_count), 0) FROM shares WHERE deleted = 0"
    ).fetchone()[0]
    total_views = db.execute(
        "SELECT COALESCE(SUM(view_count), 0) FROM shares WHERE deleted = 0"
    ).fetchone()[0]
    total_storage = db.execute(
        "SELECT COALESCE(SUM(file_size), 0) FROM shares WHERE deleted = 0"
    ).fetchone()[0]

    return {
        "total": total,
        "active": active,
        "expired": expired,
        "exhausted": exhausted,
        "disabled": disabled,
        "total_downloads": total_downloads,
        "total_views": total_views,
        "total_storage": total_storage,
    }


def get_activity_log(db, limit=50, offset=0):
    rows = db.execute(
        """SELECT a.*, s.original_filename, s.access_code
           FROM activity a
           LEFT JOIN shares s ON a.share_id = s.id
           ORDER BY a.created_at DESC
           LIMIT ? OFFSET ?""",
        (limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


def create_twofa_code(db, code_hash, expires_at):
    cursor = db.execute(
        "INSERT INTO twofa_codes (code_hash, created_at, expires_at, used, attempts) VALUES (?, ?, ?, 0, 0)",
        (code_hash, now_iso(), expires_at),
    )
    db.commit()
    return cursor.lastrowid


def verify_twofa_code(db, code_id, max_attempts=5):
    now = now_iso()
    row = db.execute(
        "SELECT * FROM twofa_codes WHERE id = ? AND used = 0 AND expires_at > ?",
        (code_id, now),
    ).fetchone()
    if not row:
        return False
    if row["attempts"] >= max_attempts:
        db.execute("UPDATE twofa_codes SET used = 1 WHERE id = ?", (code_id,))
        db.commit()
        return False
    db.execute(
        "UPDATE twofa_codes SET attempts = attempts + 1 WHERE id = ?",
        (code_id,),
    )
    db.commit()
    return True


def consume_twofa_code(db, code_id):
    db.execute("UPDATE twofa_codes SET used = 1 WHERE id = ?", (code_id,))
    db.commit()


def cleanup_expired_twofa_codes(db):
    db.execute("DELETE FROM twofa_codes WHERE expires_at <= ? OR used = 1", (now_iso(),))
    db.commit()


def cleanup_old_activity(db, days=90):
    cutoff = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    from datetime import timedelta
    cutoff = (cutoff - timedelta(days=days)).isoformat()
    db.execute("DELETE FROM activity WHERE created_at < ?", (cutoff,))
    db.commit()
