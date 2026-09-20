import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "")
    DATABASE_PATH = os.environ.get("DATABASE_PATH", "data/share.db")
    STORAGE_PATH = os.environ.get("STORAGE_PATH", "data/files")
    MAX_UPLOAD_SIZE = int(os.environ.get("MAX_UPLOAD_SIZE", 10737418240))
    SESSION_LIFETIME = int(os.environ.get("SESSION_LIFETIME", 3600))
    CLEANUP_INTERVAL = int(os.environ.get("CLEANUP_INTERVAL", 3600))
    FILE_RETENTION_DAYS = int(os.environ.get("FILE_RETENTION_DAYS", 7))

    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")
    DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

    BASE_URL = os.environ.get("BASE_URL", "https://share.xangey.dev")

    RATELIMIT_STORAGE_URI = "memory://"
    RATELIMIT_DEFAULT = "60/minute"

    SHARE_CODE_LENGTH = 10
    CODE_CHARSET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    MAX_CUSTOM_CODE_LENGTH = 32
    MIN_CUSTOM_CODE_LENGTH = 4

    TWO_FA_CODE_LENGTH = 6
    TWO_FA_CODE_LIFETIME = 300  # 5 minutes
    TWO_FA_MAX_ATTEMPTS = 5

    SECURE_COOKIES = os.environ.get("SECURE_COOKIES", "1") == "1"
    TESTING = os.environ.get("TESTING", "0") == "1"

    @property
    def database_dir(self):
        return os.path.dirname(self.DATABASE_PATH)

    @property
    def storage_dir(self):
        return self.STORAGE_PATH

    def validate(self):
        errors = []
        if not self.SECRET_KEY:
            errors.append("SECRET_KEY is required")
        if not self.ADMIN_PASSWORD_HASH:
            errors.append("ADMIN_PASSWORD_HASH is required")
        if not self.DISCORD_WEBHOOK_URL:
            errors.append("DISCORD_WEBHOOK_URL is required")
        if len(self.SECRET_KEY) < 32:
            errors.append("SECRET_KEY must be at least 32 characters")
        return errors
