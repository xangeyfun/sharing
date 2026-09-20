import threading
import logging
import os
from datetime import datetime, timezone, timedelta

from app.config import Config


logger = logging.getLogger(__name__)

_thread = None
_stop = threading.Event()


def _db_path():
    return Config.DATABASE_PATH


def _cleanup_once():
    import sqlite3
    try:
        conn = sqlite3.connect(_db_path(), timeout=30)
        conn.row_factory = sqlite3.Row
        now = datetime.now(timezone.utc).isoformat()

        conn.execute("DELETE FROM twofa_codes WHERE expires_at <= ? OR used = 1", (now,))
        conn.commit()

        expired = conn.execute(
            "SELECT id FROM shares WHERE deleted = 0 AND expires_at < ? AND expiry_notified = 0",
            (now,),
        ).fetchall()
        for row in expired:
            conn.execute(
                "INSERT INTO activity (share_id, event_type, event_detail, created_at) VALUES (?, 'share_expired', ?, ?)",
                (row["id"], "Share expired", now),
            )
            conn.execute("UPDATE shares SET expiry_notified = 1 WHERE id = ?", (row["id"],))
        if expired:
            conn.commit()

        cutoff = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
        conn.execute("DELETE FROM activity WHERE created_at < ?", (cutoff,))
        conn.commit()

        retention = datetime.now(timezone.utc) - timedelta(days=Config.FILE_RETENTION_DAYS)
        cutoff = retention.isoformat()

        rows = conn.execute(
            "SELECT id, storage_name FROM shares WHERE expires_at < ?",
            (cutoff,),
        ).fetchall()

        for row in rows:
            path = os.path.join(Config.STORAGE_PATH, row["storage_name"])
            if os.path.isfile(path):
                os.remove(path)
            conn.execute("UPDATE shares SET deleted = 1 WHERE id = ?", (row["id"],))
        conn.commit()

        conn.close()
    except Exception as exc:
        logger.warning("Cleanup failed: %s", exc)


def _run_loop():
    while not _stop.wait(Config.CLEANUP_INTERVAL):
        _cleanup_once()


def start_maintenance_thread(app):
    global _thread
    if app.config.get("TESTING"):
        return
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_run_loop, daemon=True, name="maintenance")
    _thread.start()