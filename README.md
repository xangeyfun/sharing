# share.xangey.dev

A self hosted file sharing service. Upload a file, configure how the share works, and get a short link like `https://share.xangey.dev/s/Oo87wnf88f` to send to friends.

## Features

- Admin panel protected by password authentication plus Discord based 2FA
- Temporary shares with expiration and optional download limits plus optional page-view limits
- Device and recipient friendly download pages with inline previews (text, images, video, audio, PDF)
- Download and view limits enforced atomically on the server, resistant to race conditions
- Expired, exhausted and disabled shares are rejected server side, the button is not merely hidden
- All uploads stored outside the public web directory under random names
- Large files are streamed, never loaded fully into memory
- Share management, filtering, sorting, activity log and usage statistics
- Dark blue interface with an accent palette matched to xangey.dev

## Tech stack

- Python 3.11+ and Flask
- SQLite via the standard `sqlite3` module
- Argon2id password hashing (`argon2-cffi`)
- Gunicorn for production serving
- No JavaScript framework. A small amount of vanilla JS.

## Requirements

- Python 3.11 or newer
- A Discord webhook URL for delivering 2FA codes
- A reverse proxy (nginx recommended) for TLS termination

## Installation

```bash
git clone <your-repo-url> share
cd share
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

For development and running the test suite:

```bash
.venv/bin/pip install -r requirements-dev.txt
```

## Configuration

All configuration is done through environment variables. Copy the example file and fill it in:

```bash
cp .env.example .env
```

Generate a secret key and an admin password hash:

```bash
.venv/bin/python -c "import secrets; print(secrets.token_hex(32))"
.venv/bin/python -c "from argon2 import PasswordHasher; print(PasswordHasher().hash('choose-a-long-password'))"
```

The application loads `.env` automatically through `python-dotenv`.

### Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `ADMIN_USERNAME` | yes | `admin` | Admin login username |
| `ADMIN_PASSWORD_HASH` | yes | | Argon2id hash of the admin password |
| `SECRET_KEY` | yes | | Secret used to sign session cookies, at least 32 characters |
| `DISCORD_WEBHOOK_URL` | yes | | Discord webhook that receives 2FA codes |
| `BASE_URL` | | `https://share.xangey.dev` | Public base URL used when building share links |
| `SECURE_COOKIES` | | `1` | Set to `0` only for local development over plain HTTP |
| `DATABASE_PATH` | | `data/share.db` | SQLite database path |
| `STORAGE_PATH` | | `data/files` | Directory where uploaded files are stored |
| `MAX_UPLOAD_SIZE` | | `10737418240` (10 GB) | Maximum upload size in bytes |
| `SESSION_LIFETIME` | | `3600` | Admin session lifetime in seconds |
| `CLEANUP_INTERVAL` | | `3600` | Background cleanup interval in seconds |
| `FILE_RETENTION_DAYS` | | `7` | Days a file is kept after its share expires |

Never commit `.env`. It is gitignored.

## Database setup

The database initializes automatically on first start. No manual migration step is required for a fresh install.

To (re)create the schema manually:

```bash
.venv/bin/python - <<'EOF'
from app.database import init_db
from app.config import Config
init_db(Config.DATABASE_PATH)
EOF
```

SQLite is used with WAL mode, foreign keys enabled and a busy timeout. Download and page-view limits are independent and each is enforced with a single atomic `UPDATE`, so concurrent downloads or views can never overshoot a share limit. A visitor who uses the last page view can still download the file (up to the download limit), and vice versa. Previews do not consume either counter; only the share page and the actual download are metered.

## File storage

Uploaded files are stored under `STORAGE_PATH` (default `data/files`) using randomly generated names. The original filename is preserved in the database and used only for display and the download `Content-Disposition` header via RFC 5987 encoding.

Important constraints:

- `STORAGE_PATH` must never be inside a directory your web server serves.
- `DATABASE_PATH` should also be outside the public web root.
- Downloads stream through Flask in 64 KB chunks; the whole file is never buffered in memory.

## Running locally

```bash
cp .env.example .env
# edit .env, set SECURE_COOKIES=0 for local HTTP
.venv/bin/python wsgi.py
```

Visit `http://127.0.0.1:8003/admin`.

Alternatively run through gunicorn:

```bash
.venv/bin/gunicorn -w 2 -t 300 -b 127.0.0.1:8003 wsgi:app
```

## Discord 2FA setup

1. In a Discord server you control, open Server Settings, App Settings, Integrations, Webhooks.
2. Create a new webhook, optionally set a name and channel.
3. Copy the webhook URL into `DISCORD_WEBHOOK_URL`.
4. Restrict who can use the webhook if your Discord server settings allow it. The webhook URL is a secret, treat it like one.

How it works:

- A successful password check generates a random 6 character one time code, hashed with Argon2id before storage.
- The code is sent to the configured Discord webhook.
- The code expires after 5 minutes, works only once, and is invalidated after five failed attempts per code and after five attempts per IP within 5 minutes.

Passwords, 2FA codes and the discord webhook URL are never written to logs.

## Production deployment

1. Clone the repository to `/srv/share` and install dependencies.
2. Create a system user or use `www-data`.
3. Create `/srv/share/.env` as owned by that user, with production values and `SECURE_COOKIES=1`.
4. Create the `data` directory and make it writable:

```bash
mkdir -p /srv/share/data/files
chown -R www-data:www-data /srv/share/data
```

5. Copy and adapt the systemd unit:

```bash
cp deploy/share.service.example /etc/systemd/system/share.service
systemctl daemon-reload
systemctl enable --now share
```

6. Copy and adapt the nginx config:

```bash
cp deploy/nginx.conf.example /etc/nginx/sites-available/share.xangey.dev
ln -s /etc/nginx/sites-available/share.xangey.dev /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

Use Certbot to obtain certificates for `share.xangey.dev`:

```bash
certbot certonly --webroot -w /var/www/html -d share.xangey.dev
```

### Reverse proxy notes

- `ProxyFix` is enabled, so the app honors `X-Forwarded-Proto`. Make sure nginx sets it, otherwise secure cookies will not be issued.
- `X-Forwarded-For` is used for rate limiting identities. Ensure no untrusted client can spoof it before it reaches the app.
- Set `client_max_body_size` in nginx to match or exceed `MAX_UPLOAD_SIZE`.
- Disable proxy request buffering for large uploads and downloads.
- Tune `proxy_read_timeout` / `proxy_send_timeout` for slow connections.

## Running tests

```bash
.venv/bin/python -m pytest tests/ -q
```

The suite covers:

- Full login plus Discord 2FA flow
- Expired, disabled, exhausted and deleted share rejection
- Download and view limit counting and atomic enforcement under concurrent access
- Invalid and nonexistent share codes
- Upload validation, path traversal and filename safety
- CSRF rejection of unauthenticated state changes
- Rate limiting on authentication
- Large file streaming
- Delete and disable operations

## Security model

- Passwords hashed with Argon2id, no secrets in source code
- 2FA codes generated with `secrets`, hashed, single use and short lived
- Random share codes use a cryptographically secure generator
- CSRF tokens validated for every state changing request
- HttpOnly, SameSite=LAX, Secure session cookies
- Strict CSP, `X-Content-Type-Options`, `X-Frame-Options`, referrer policy
- Rate limiting on login, 2FA, uploads and the public API
- Admin routes always require an authenticated session
- Database path and storage path are never exposed to the client

## License

Private project for xangey.dev. See LICENSE if present.