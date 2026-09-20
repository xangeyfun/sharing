import os
import secrets
import mimetypes
from werkzeug.utils import secure_filename


def init_storage(storage_path):
    os.makedirs(storage_path, exist_ok=True)


def generate_storage_name():
    return secrets.token_hex(16)


def save_file(storage_path, file_storage, original_filename):
    storage_name = generate_storage_name()
    dest = os.path.join(storage_path, storage_name)
    file_storage.save(dest)
    file_size = os.path.getsize(dest)
    return storage_name, file_size


def get_file_path(storage_path, storage_name):
    safe = secure_filename(storage_name)
    if safe != storage_name:
        return None
    path = os.path.join(storage_path, storage_name)
    if not os.path.isfile(path):
        return None
    real_storage = os.path.realpath(storage_path)
    real_file = os.path.realpath(path)
    if not real_file.startswith(real_storage + os.sep) and real_file != real_storage:
        return None
    return path


def delete_file(storage_path, storage_name):
    path = get_file_path(storage_path, storage_name)
    if path:
        os.remove(path)


def get_content_type(filename):
    content_type, _ = mimetypes.guess_type(filename)
    return content_type or "application/octet-stream"


def content_disposition(filename, disposition="attachment"):
    from urllib.parse import quote
    safe = "".join(c for c in filename if c not in '"\\\r\n')
    encoded = quote(filename, safe="")
    return f"{disposition}; filename=\"{safe}\"; filename*=UTF-8''{encoded}"


_TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".log", ".xml", ".yaml", ".yml",
    ".ini", ".conf", ".cfg", ".toml", ".html", ".htm", ".js", ".css",
    ".py", ".sh", ".rb", ".go", ".rs", ".java", ".c", ".h", ".cpp",
}


def get_preview_type(filename):
    content_type = get_content_type(filename)
    if content_type.startswith("text/") or content_type in (
        "application/json", "application/xml", "application/javascript",
        "application/x-javascript",
    ):
        return "text"
    if content_type.startswith("image/"):
        return "image"
    if content_type.startswith("video/"):
        return "video"
    if content_type.startswith("audio/"):
        return "audio"
    if content_type == "application/pdf":
        return "pdf"
    ext = os.path.splitext(filename)[1].lower()
    if content_type == "application/octet-stream" and ext in _TEXT_EXTENSIONS:
        return "text"
    return None


def get_text_preview(file_path, max_bytes=65536):
    with open(file_path, "rb") as f:
        data = f.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="replace")
    return text, truncated


def validate_upload_filename(filename):
    if not filename or not filename.strip():
        return False, "No filename provided"
    if len(filename) > 255:
        return False, "Filename too long"
    dangerous = ["..", "/", "\\", "\x00"]
    for d in dangerous:
        if d in filename:
            return False, "Invalid filename"
    return True, None
