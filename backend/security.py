"""
Security Utilities and Helpers
Implements redirect validation, CSV sanitization against formula injection,
secure token generation, Pillow image validation/sanitization, and password token management.
"""

import io
import re
import secrets
from urllib.parse import urlparse

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from PIL import Image

# Regex for forbidden CSV formula injection characters
CSV_INJECTION_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def is_safe_redirect(target: str | None) -> bool:
    """
    Validate that a redirect target is a safe relative URL on the current host.
    Rejects protocol-relative URLs (e.g. //evil.com), backslashes, and external schemes.
    """
    if not target:
        return False

    target = target.strip()
    # Reject protocol-relative or backslash-based URL evasion
    if target.startswith('//') or target.startswith('\\') or '/\\' in target:
        return False

    parsed = urlparse(target)
    # Target must not specify a scheme or network location (must be purely relative path)
    if parsed.scheme or parsed.netloc:
        return False

    # Target must begin with a single slash
    if not target.startswith('/'):
        return False

    return True


def sanitize_csv_cell(value: str | None) -> str:
    """
    Sanitize a cell value for CSV export to prevent formula/command injection attacks.
    If the value starts with =, +, -, @, \\t, or \\r, prefix with a single quote.
    """
    if value is None:
        return ""
    val_str = str(value)
    if val_str and val_str[0] in CSV_INJECTION_PREFIXES:
        return f"'{val_str}"
    return val_str


def desanitize_csv_cell(value: str | None) -> str:
    """Strip defensive single-quote prefix added during CSV sanitization if present."""
    if not value:
        return ""
    val_str = str(value).strip()
    if val_str.startswith("'") and len(val_str) > 1 and val_str[1] in CSV_INJECTION_PREFIXES:
        return val_str[1:]
    return val_str


def generate_secure_token(nbytes: int = 24) -> str:
    """Generate a cryptographically secure URL-safe random string."""
    return secrets.token_urlsafe(nbytes)


def generate_temp_password(length: int = 14, username: str | None = None) -> str:
    """Generate a high-entropy random password guaranteed to satisfy complexity requirements."""
    upper = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    lower = "abcdefghijkmnopqrstuvwxyz"
    digits = "23456789"
    special = "!@#$%^&*"
    all_chars = upper + lower + digits + special

    for _ in range(50):
        pwd = [
            secrets.choice(upper),
            secrets.choice(lower),
            secrets.choice(digits),
            secrets.choice(special),
        ] + [secrets.choice(all_chars) for _ in range(max(length - 4, 4))]
        secrets.SystemRandom().shuffle(pwd)
        candidate = "".join(pwd)
        is_valid, _ = validate_password_strength(candidate, username=username)
        if is_valid:
            return candidate
    return candidate


def get_serializer(secret_key: str | None = None) -> URLSafeTimedSerializer:
    """Return itsdangerous serializer configured with application SECRET_KEY."""
    key = secret_key or (current_app.config['SECRET_KEY'] if current_app else 'default-key')
    return URLSafeTimedSerializer(key, salt='student-system-password-reset')


def generate_password_reset_token(user_id: int, session_version: int = 0, secret_key: str | None = None) -> str:
    """Generate a signed, timed token valid for password setup/reset (24 hours)."""
    s = get_serializer(secret_key)
    return s.dumps({'user_id': user_id, 'v': session_version})


def verify_password_reset_token(token: str, secret_key: str | None = None, max_age: int = 86400, **kwargs) -> int | None:
    """
    Verify and decode a signed password token.
    Returns decoded user_id (int) or None if expired/invalid.
    """
    data = verify_password_reset_token_data(token, secret_key, max_age=kwargs.get('max_age_seconds', max_age))
    return data.get('user_id') if data else None


def verify_password_reset_token_data(token: str, secret_key: str | None = None, max_age: int = 86400) -> dict | None:
    """Verify signed password reset token and return dictionary containing user_id and session_version."""
    s = get_serializer(secret_key)
    try:
        data = s.loads(token, max_age=max_age)
        if isinstance(data, dict):
            return data
        return {'user_id': int(data), 'v': None}
    except (SignatureExpired, BadSignature, Exception):
        return None


def validate_password_strength(password: str, username: str | None = None) -> tuple[bool, str | None]:
    """
    Validate password meets security complexity policy:
    - At least 8 characters
    - Not identical to the username (case-insensitive)
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    - At least one special character
    """
    if not password or len(password) < 8:
        return False, "Password must be at least 8 characters in length."

    if username:
        u_clean = str(username).strip().lower()
        p_clean = str(password).strip().lower()
        if u_clean and p_clean == u_clean:
            return False, "Password cannot be the same as your username."

    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter."
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter."
    if not re.search(r"\d", password):
        return False, "Password must contain at least one digit."
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
        return False, "Password must contain at least one special character (!@#$%^&*...)."
    return True, None


ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
MAX_IMAGE_SIZE_BYTES = 3 * 1024 * 1024  # 3 MB


def validate_and_sanitize_image(
    file_storage,
    max_dimensions: tuple[int, int] = (2000, 2000)
) -> tuple[bool, str | None, io.BytesIO | None]:
    """
    Validate uploaded image using Pillow:
    1. Server-side check of file extension against allowed whitelist (png, jpg, jpeg, webp).
    2. Server-side check of file size against 3MB maximum limit.
    3. Verify file content matches a valid raster image format using real byte inspection.
    4. Strip metadata (EXIF/GPS/comments) to prevent information leakage.
    5. Enforce maximum resolution to mitigate image-decompression bombs.
    6. Re-encode as clean JPEG, PNG, or WEBP.

    Returns:
        tuple: (is_valid: bool, error_msg: Optional[str], sanitized_buffer: Optional[io.BytesIO])
    """
    if not file_storage or not file_storage.filename:
        return False, "No file provided", None

    # 1. Server-side check of file extension
    filename = file_storage.filename.strip()
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return False, f"Invalid file extension '.{ext}'. Allowed extensions: png, jpg, jpeg, webp.", None

    try:
        file_bytes = file_storage.read()
        file_storage.seek(0)

        # 2. Server-side check of file size (max 3MB)
        if len(file_bytes) > MAX_IMAGE_SIZE_BYTES:
            size_mb = round(len(file_bytes) / (1024 * 1024), 2)
            return False, f"Uploaded file exceeds the maximum 3MB limit ({size_mb}MB uploaded).", None

        img_buffer = io.BytesIO(file_bytes)

        # 3. Open and verify real image structure
        with Image.open(img_buffer) as img:
            img.verify()

        # Reopen for transformation/re-encoding
        img_buffer.seek(0)
        with Image.open(img_buffer) as img:
            img_format = (img.format or '').upper()
            if img_format not in ('JPEG', 'JPG', 'PNG', 'WEBP'):
                return False, f"Unsupported image format: {img_format}. Allowed: JPEG, PNG, WEBP.", None

            width, height = img.size
            if width > max_dimensions[0] or height > max_dimensions[1]:
                # Resize proportionally
                img.thumbnail(max_dimensions, Image.Resampling.LANCZOS)

            # Strip EXIF and re-encode
            output_buffer = io.BytesIO()
            if img_format in ('JPEG', 'JPG'):
                rgb_img = img.convert('RGB')
                rgb_img.save(output_buffer, format='JPEG', quality=85, optimize=True)
            elif img_format == 'WEBP':
                img.save(output_buffer, format='WEBP', quality=85)
            else:
                img.save(output_buffer, format='PNG', optimize=True)

            output_buffer.seek(0)
            return True, None, output_buffer

    except Exception as e:
        return False, f"Invalid or corrupted image file: {e!s}", None
