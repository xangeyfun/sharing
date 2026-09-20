import os
from datetime import datetime, timezone

from flask import (
    Blueprint, render_template, g, Response, request
)
from app.config import Config
from app.database import get_share_by_code, try_download_share, try_view_share, get_share_status, log_activity
from app.storage import (
    get_file_path, get_content_type, get_preview_type, get_text_preview, content_disposition
)
from app.ratelimit import rate_limit_api, rate_limit_media

share_bp = Blueprint("share", __name__)


@share_bp.route("/s/<code>")
@rate_limit_api
def access_share(code):
    db = g.db
    share = get_share_by_code(db, code)

    if not share:
        return render_template("public/share_invalid.html"), 404

    status = get_share_status(share)

    if status in ("deleted", "disabled", "expired"):
        return render_template("public/share_invalid.html", status=status), 404

    if not try_view_share(db, share["id"]):
        return render_template("public/share_invalid.html", status="exhausted"), 404

    share = get_share_by_code(db, code)

    remaining_downloads = None
    if share["download_limit"] is not None:
        remaining_downloads = max(0, share["download_limit"] - share["download_count"])

    remaining_views = None
    if share["view_limit"] is not None:
        remaining_views = max(0, share["view_limit"] - share["view_count"])

    expires_at = datetime.fromisoformat(share["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    preview_type = get_preview_type(share["original_filename"])
    text_preview = None
    text_truncated = False
    if preview_type == "text":
        file_path = get_file_path(Config.STORAGE_PATH, share["storage_name"])
        if file_path:
            text_preview, text_truncated = get_text_preview(file_path)
        else:
            preview_type = None

    return render_template("public/share_info.html",
        share=share,
        remaining=remaining_downloads,
        remaining_views=remaining_views,
        expires_at=expires_at,
        preview_type=preview_type,
        text_preview=text_preview,
        text_truncated=text_truncated,
    )


def _stream_file(file_path, file_size, content_type, disposition):
    headers = {
        "Content-Type": content_type,
        "Content-Disposition": disposition,
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
    }

    range_header = request.headers.get("Range")
    status = 200
    start, end = 0, file_size - 1

    if range_header and range_header.startswith("bytes="):
        spec = range_header[len("bytes="):]
        first, _, last = spec.partition("-")
        try:
            if not first:
                suffix_len = int(last)
                if suffix_len <= 0:
                    return Response(status=416, headers={**headers, "Content-Range": f"bytes */{file_size}"})
                start = max(0, file_size - suffix_len)
                end = file_size - 1
            else:
                start = int(first)
                end = int(last) if last else file_size - 1
        except ValueError:
            return Response(status=416, headers={**headers, "Content-Range": f"bytes */{file_size}"})

        if start < 0 or start >= file_size:
            return Response(status=416, headers={**headers, "Content-Range": f"bytes */{file_size}"})
        end = min(end, file_size - 1)
        if end < start:
            return Response(status=416, headers={**headers, "Content-Range": f"bytes */{file_size}"})
        status = 206

    length = end - start + 1
    headers["Content-Length"] = str(length)
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    def generate():
        with open(file_path, "rb") as f:
            if start:
                f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return Response(generate(), status=status, headers=headers)


@share_bp.route("/s/<code>/download")
@rate_limit_api
def download_share(code):
    db = g.db
    share = get_share_by_code(db, code)

    if not share:
        return render_template("public/share_invalid.html"), 404

    file_path = get_file_path(Config.STORAGE_PATH, share["storage_name"])
    if not file_path:
        return render_template("public/share_invalid.html", status="unavailable"), 404

    access_granted = try_download_share(db, share["id"])
    if not access_granted:
        return render_template("public/share_invalid.html", status="unavailable"), 404

    log_activity(db, share["id"], "share_accessed", "File downloaded")

    content_type = get_content_type(share["original_filename"])
    file_size = os.path.getsize(file_path)

    return _stream_file(
        file_path,
        file_size,
        content_type,
        content_disposition(share["original_filename"]),
    )


@share_bp.route("/s/<code>/preview")
@rate_limit_media
def preview_share(code):
    db = g.db
    share = get_share_by_code(db, code)

    if not share:
        return render_template("public/share_invalid.html"), 404

    file_path = get_file_path(Config.STORAGE_PATH, share["storage_name"])
    if not file_path:
        return render_template("public/share_invalid.html", status="unavailable"), 404

    status = get_share_status(share)
    if status in ("deleted", "disabled", "expired"):
        return render_template("public/share_invalid.html"), 404

    content_type = get_content_type(share["original_filename"])
    file_size = os.path.getsize(file_path)

    return _stream_file(
        file_path,
        file_size,
        content_type,
        content_disposition(share["original_filename"], disposition="inline"),
    )