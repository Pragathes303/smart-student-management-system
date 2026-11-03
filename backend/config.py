"""
Application Configuration Module
Loads settings from environment variables (.env) and provides
centralized, hardened application settings for sessions, CSRF, rate-limiting, and storage.
"""

import os

from dotenv import load_dotenv

# Base paths
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)

# Load environment variables from .env file in project root
dotenv_path = os.path.join(PROJECT_ROOT, '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)


class Config:
    """Base Configuration with security defaults."""

    # Project Root
    BASE_DIR = PROJECT_ROOT

    # Environment
    ENV = os.getenv('FLASK_ENV', 'development').lower()

    # DEBUG is strictly OFF by default; enabled only if explicitly set to truthy values ('1', 'true', 'yes', 'on')
    _debug_env = os.getenv('FLASK_DEBUG', os.getenv('DEBUG', '0')).strip().lower()
    DEBUG = _debug_env in ('1', 'true', 'yes', 'on')

    # Secret key for session signing and CSRF protection
    SECRET_KEY = os.getenv('SECRET_KEY', 'default-dev-secret-key-change-in-production')
    if ENV == 'production':
        if not SECRET_KEY or SECRET_KEY == 'default-dev-secret-key-change-in-production' or len(SECRET_KEY) < 32:
            raise RuntimeError(
                "CRITICAL SECURITY CONFIGURATION: Production deployment requires a secure, "
                "unique SECRET_KEY of at least 32 characters in .env."
            )

    # Base URL for email notification links and web hooks
    APP_BASE_URL = os.getenv('APP_BASE_URL', 'http://127.0.0.1:5000').rstrip('/')

    # Reverse proxy trusted count (1 behind Render, Nginx, or AWS ALB)
    TRUSTED_PROXIES = int(os.getenv('TRUSTED_PROXIES', '1' if ENV == 'production' else '0'))

    # CSRF Protection via Flask-WTF
    WTF_CSRF_ENABLED = os.getenv('WTF_CSRF_ENABLED', '1') == '1'
    WTF_CSRF_TIME_LIMIT = int(os.getenv('WTF_CSRF_TIME_LIMIT', '86400'))

    # Database Configuration (Supports SQLite and DATABASE_URL for Cloud/PostgreSQL)
    _db_url = os.getenv('DATABASE_URL')
    if _db_url:
        # Standardize Heroku/Render legacy 'postgres://' prefix to 'postgresql://'
        if _db_url.startswith('postgres://'):
            _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
        DATABASE_URL = _db_url
        if _db_url.startswith('sqlite:///'):
            _db_path = _db_url.replace('sqlite:///', '')
        else:
            _db_path = os.getenv('DATABASE_PATH', os.path.join(PROJECT_ROOT, 'database', 'students.db'))
    else:
        DATABASE_URL = None
        _db_path = os.getenv('DATABASE_PATH', os.path.join(PROJECT_ROOT, 'database', 'students.db'))

    if not os.path.isabs(_db_path):
        DATABASE_PATH = os.path.join(PROJECT_ROOT, _db_path)
    else:
        DATABASE_PATH = _db_path

    # File Upload Settings (Supports Local, Persistent Disks like /var/data, and S3)
    _default_upload = os.path.join(PROJECT_ROOT, 'frontend', 'static', 'uploads')
    _upload_folder = os.getenv('UPLOAD_FOLDER', os.getenv('PERSISTENT_DISK_PATH', _default_upload))
    if not os.path.isabs(_upload_folder):
        UPLOAD_FOLDER = os.path.join(PROJECT_ROOT, _upload_folder)
    else:
        UPLOAD_FOLDER = _upload_folder
    MAX_CONTENT_LENGTH = 3 * 1024 * 1024  # 3 MB limit for file uploads
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}

    # Storage Backend: 'local' (default) or 's3' (AWS EC2 / S3 integration)
    STORAGE_BACKEND = os.getenv('STORAGE_BACKEND', 'local').lower()

    # AWS S3 Settings (Required only when STORAGE_BACKEND=s3)
    AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID', '')
    AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY', '')
    AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
    AWS_S3_BUCKET = os.getenv('AWS_S3_BUCKET', '')
    AWS_S3_PUBLIC_READ = os.getenv('AWS_S3_PUBLIC_READ', '0') == '1'

    # Rate Limiting (Flask-Limiter)
    RATELIMIT_STORAGE_URI = os.getenv('RATELIMIT_STORAGE_URI', os.getenv('REDIS_URL', 'memory://'))
    RATELIMIT_STRATEGY = 'moving-window'
    RATELIMIT_HEADERS_ENABLED = True

    # Performance & Caching (Phase 6)
    DASHBOARD_CACHE_TTL = int(os.getenv('DASHBOARD_CACHE_TTL', '180'))  # Default 3 minutes (180s)

    # Application Logging Settings
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
    _log_path = os.getenv('LOG_FILE', os.path.join(PROJECT_ROOT, 'logs', 'app.log'))
    if not os.path.isabs(_log_path):
        LOG_FILE = os.path.join(PROJECT_ROOT, _log_path)
    else:
        LOG_FILE = _log_path

    # Session Security Flags
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = (ENV == 'production') or (os.getenv('SESSION_COOKIE_SECURE', '0') == '1')
    SESSION_COOKIE_NAME = '__Host-session' if (ENV == 'production') else 'session'
    PERMANENT_SESSION_LIFETIME = int(os.getenv('SESSION_LIFETIME_SECONDS', 1800))  # 30 minutes idle timeout

    # Institutional Defaults
    DEFAULT_TOTAL_CLASSES = int(os.getenv('DEFAULT_TOTAL_CLASSES', '60'))
    DEFAULT_ATTENDED_CLASSES = int(os.getenv('DEFAULT_ATTENDED_CLASSES', '52'))

    # Defined Departments Whitelist
    DEPARTMENTS = [
        "Computer Science",
        "Information Technology",
        "Electronics & Comm.",
        "Electrical Engineering",
        "Mechanical Engineering",
        "Civil Engineering",
        "Data Science",
        "Artificial Intelligence"
    ]
