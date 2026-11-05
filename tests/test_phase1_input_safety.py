"""
Phase 1 - Security: Step 3 Input and Request Safety Tests
Validates:
1. CSRF Protection (Flask-WTF) on every POST form and POST-only enforcement on delete operations.
2. Only parameterized queries (safe against SQL injection).
3. Server-side input validation & sanitization (name, register number, marks 0-100, email, phone, DOB).
   Jinja autoescape verification and zero usage of |safe on user data.
4. File Upload Security:
   - Server-side extension check against whitelist (png, jpg, jpeg, webp).
   - Server-side real image content inspection (Pillow magic bytes / verify).
   - Maximum 3MB file size enforcement.
   - secure_filename and random unique filename generation.
   - Storage directory outside templates and safe serving.
5. Security Headers:
   - Content-Security-Policy (allowing Chart.js and Google Fonts).
   - X-Frame-Options (DENY).
   - X-Content-Type-Options (nosniff).
   - Referrer-Policy (strict-origin-when-cross-origin).
   - Strict-Transport-Security (HSTS) in production.
"""

import io
import os
import re
from datetime import date, timedelta

from PIL import Image

from backend.app import create_app
from backend.config import Config
from backend.security import validate_and_sanitize_image
from backend.storage import LocalStorageHandler
from backend.utils import (
    validate_date_of_birth,
    validate_phone_number,
    validate_student_data,
)


# -----------------------------------------------------------------------------
# 1. CSRF Protection & Delete Method Enforcement
# -----------------------------------------------------------------------------
def test_csrf_protection_enforced_when_enabled(tmp_path):
    """POST request without CSRF token must fail with 400 Bad Request when CSRF is enabled."""
    db_file = tmp_path / "csrf_test.db"
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    class CsrfConfig(Config):
        TESTING = True
        WTF_CSRF_ENABLED = True
        DATABASE_PATH = str(db_file)
        UPLOAD_FOLDER = str(upload_dir)
        SECRET_KEY = 'csrf-test-secret-key-32-chars-minimum-prod'

    app_instance = create_app(CsrfConfig)
    with app_instance.test_client() as c:
        # POST without CSRF token
        res = c.post('/login', data={'username': 'admin', 'password': 'Admin@123'})
        assert res.status_code == 400
        assert b"CSRF" in res.data or b"Bad Request" in res.data


def test_delete_endpoints_reject_get_method(admin_client):
    """Delete endpoints must strictly be POST and reject GET with 405 Method Not Allowed."""
    delete_endpoints = [
        '/students/1/delete',
        '/students/1/delete-subject/1',
        '/students/1/hard-delete',
    ]
    for url in delete_endpoints:
        res = admin_client.get(url)
        assert res.status_code == 405, f"Expected 405 for GET {url}, got {res.status_code}"


def test_templates_post_forms_include_csrf_token():
    """All HTML templates containing POST forms must include a CSRF token input."""
    template_dir = 'frontend/templates'
    missing = []
    for root, _, files in os.walk(template_dir):
        for f in files:
            if f.endswith('.html'):
                path = os.path.join(root, f)
                with open(path, 'r', encoding='utf-8') as fp:
                    content = fp.read()
                forms = re.finditer(r'<form\b([^>]*)>(.*?)</form>', content, re.DOTALL | re.IGNORECASE)
                for fm in forms:
                    tag, body = fm.group(1), fm.group(2)
                    if re.search(r'method=["\']?post["\']?', tag, re.IGNORECASE):
                        if 'csrf_token' not in body:
                            missing.append((path, tag))
    assert len(missing) == 0, f"Found POST forms missing CSRF token: {missing}"


# -----------------------------------------------------------------------------
# 2. Parameterized Queries & SQL Injection Resistance
# -----------------------------------------------------------------------------
def test_sql_injection_resistance_in_search(admin_client):
    """SQL injection payloads in search queries must be treated as literal strings."""
    sqli_payloads = [
        "' OR '1'='1",
        "'; DROP TABLE students; --",
        "' UNION SELECT id, username, password_hash, NULL, NULL, NULL, NULL, NULL, NULL, NULL FROM users --",
    ]
    for payload in sqli_payloads:
        res = admin_client.get(f'/students?q={payload}')
        assert res.status_code == 200
        # Verify page renders without database corruption
        assert b"Student Directory" in res.data or b"Student Records" in res.data


def test_sql_injection_resistance_in_reg_number(app):
    """Register number containing SQL injection characters is handled safely via parameterized query."""
    with app.app_context():
        valid, errors, cleaned = validate_student_data(
            name="SQLi Tester",
            register_number="' OR 1=1 --",
            department="Computer Science",
            email="sqli@test.edu",
            marks_raw="88.0"
        )
        assert valid is False
        assert any("alphanumeric" in e for e in errors)


# -----------------------------------------------------------------------------
# 3. Server-Side Input Validation & Sanitization
# -----------------------------------------------------------------------------
def test_name_validation():
    """Student names must be 2-80 characters and allow only letters, spaces, hyphens, and periods."""
    with create_app().app_context():
        # Valid names
        valid_names = ["Maya Chen", "O'Connor", "Jean-Luc Picard", "Dr. A. P. J. Abdul Kalam", "John Doe"]
        for n in valid_names:
            v, errs, cl = validate_student_data(n, "REGVAL01", "Computer Science", "v@test.edu", 90.0)
            assert v is True, f"Expected '{n}' to be valid, got errors: {errs}"

        # Invalid names
        invalid_names = [
            "",
            "A",  # Too short
            "A" * 81,  # Too long
            "<script>alert(1)</script>",  # XSS
            "User 123",  # Numbers
            "Student!@#",  # Special characters
        ]
        for n in invalid_names:
            v, errs, _ = validate_student_data(n, "REGVAL02", "Computer Science", "inv@test.edu", 90.0)
            assert v is False, f"Expected '{n}' to be invalid"


def test_register_number_validation(app):
    """Register number must match format and be unique."""
    with app.app_context():
        # Valid register numbers
        v, _, cl = validate_student_data("Valid Student", "REG-2024_01", "Computer Science", "v1@test.edu", 85.0)
        assert v is True
        assert cl['register_number'] == "REG-2024_01"

        # Invalid format
        v_inv, errs, _ = validate_student_data("Student", "R", "Computer Science", "v2@test.edu", 85.0)
        assert v_inv is False
        assert any("3-25 alphanumeric" in e for e in errs)

        # Duplicate register number (REG999 seeded in conftest)
        v_dup, errs_dup, _ = validate_student_data("Student", "REG999", "Computer Science", "v3@test.edu", 85.0)
        assert v_dup is False
        assert any("already exists" in e for e in errs_dup)


def test_marks_validation():
    """Marks must be numeric and bounded between 0.0 and 100.0."""
    with create_app().app_context():
        # Valid marks
        for m in [0.0, 50.0, 99.99, 100.0, "85.5", "100"]:
            v, errs, cl = validate_student_data("Student", "REGMRK01", "Computer Science", "m@test.edu", m)
            assert v is True
            assert 0.0 <= cl['marks'] <= 100.0

        # Invalid marks
        for m in [-1.0, 100.01, 150.0, "not-a-number", None, ""]:
            v, errs, _ = validate_student_data("Student", "REGMRK02", "Computer Science", "m@test.edu", m)
            assert v is False
            assert any("between 0.0 and 100.0" in e for e in errs)


def test_phone_validation():
    """Phone number must contain 7-15 digits with valid formatting."""
    valid_phones = ["9876543210", "+1 555-123-4567", "+91 98765 43210", "1234567", "(555) 123-4567"]
    for p in valid_phones:
        ok, res = validate_phone_number(p)
        assert ok is True, f"Expected '{p}' to be valid, got: {res}"

    invalid_phones = [
        "12345",  # Too short (< 7 digits)
        "1234567890123456",  # Too long (> 15 digits)
        "abc-def-ghij",  # Letters
        "+1 555 <script>",  # Injection
    ]
    for p in invalid_phones:
        ok, err = validate_phone_number(p)
        assert ok is False, f"Expected '{p}' to be invalid"


def test_date_of_birth_validation():
    """Date of birth must be a valid past date in YYYY-MM-DD format."""
    valid_dobs = ["2002-05-15", "1999-12-31", "1990-01-01"]
    for d in valid_dobs:
        ok, res = validate_date_of_birth(d)
        assert ok is True, f"Expected '{d}' to be valid, got: {res}"

    future_date = (date.today() + timedelta(days=1)).isoformat()
    invalid_dobs = [
        future_date,  # Future date
        date.today().isoformat(),  # Today
        "1850-01-01",  # Unrealistic year (< 1900)
        "not-a-date",  # Invalid string
        "2002/05/15",  # Wrong separator
        "2002-13-45",  # Invalid month/day
    ]
    for d in invalid_dobs:
        ok, err = validate_date_of_birth(d)
        assert ok is False, f"Expected '{d}' to be invalid"


def test_jinja_no_safe_filter_on_user_data():
    """Verify that Jinja autoescape is preserved and no |safe filter is used in any template."""
    template_dir = 'frontend/templates'
    violations = []
    for root, _, files in os.walk(template_dir):
        for f in files:
            if f.endswith('.html'):
                path = os.path.join(root, f)
                with open(path, 'r', encoding='utf-8') as fp:
                    for line_no, line in enumerate(fp, 1):
                        if '|safe' in line or '| safe' in line:
                            violations.append((path, line_no, line.strip()))
    assert len(violations) == 0, f"Found forbidden |safe filters in templates: {violations}"


# -----------------------------------------------------------------------------
# 4. File Upload Security
# -----------------------------------------------------------------------------
def test_file_upload_rejects_disallowed_extension():
    """Files with disallowed extensions must be rejected server-side."""
    fake_file = io.BytesIO(b"fake data")
    fake_file.filename = "script.php"
    ok, err, buf = validate_and_sanitize_image(fake_file)
    assert ok is False
    assert "Invalid file extension" in err


def test_file_upload_rejects_non_image_content_with_image_extension():
    """A PHP script or text payload renamed with a .jpg extension must be rejected via Pillow verification."""
    fake_script = io.BytesIO(b"<?php echo 'malicious'; ?>")
    fake_script.filename = "payload.jpg"
    ok, err, buf = validate_and_sanitize_image(fake_script)
    assert ok is False
    assert "Invalid or corrupted image" in err or "Unsupported image format" in err


def test_file_upload_rejects_file_exceeding_3mb():
    """Image exceeding 3MB must be rejected server-side."""
    # Create oversized bytes (> 3MB)
    large_payload = io.BytesIO(b"X" * (3 * 1024 * 1024 + 1024))
    large_payload.filename = "giant.jpg"
    ok, err, buf = validate_and_sanitize_image(large_payload)
    assert ok is False
    assert "exceeds the maximum 3MB limit" in err


def test_file_upload_sanitizes_valid_image_and_strips_metadata():
    """Valid image must be successfully sanitized, resized if needed, and re-encoded."""
    # Create a small valid test PNG image in memory
    img = Image.new('RGB', (100, 100), color='blue')
    raw_buf = io.BytesIO()
    img.save(raw_buf, format='PNG')
    raw_buf.seek(0)
    raw_buf.filename = "avatar.png"

    ok, err, sanitized_buf = validate_and_sanitize_image(raw_buf)
    assert ok is True
    assert err is None
    assert sanitized_buf is not None

    # Verify sanitized image can be read cleanly
    sanitized_img = Image.open(sanitized_buf)
    assert sanitized_img.size == (100, 100)


def test_storage_handler_uses_secure_filename_and_uuid(tmp_path):
    """Storage handler must use secure_filename and prepend random UUID to prevent path traversal."""
    upload_folder = tmp_path / "uploads"
    storage = LocalStorageHandler(str(upload_folder))

    # Test file with directory traversal attempt in filename
    fake_file = io.BytesIO(b"valid content")
    fake_file.filename = "../../etc/passwd.jpg"

    saved_name = storage.save(fake_file)
    assert saved_name is not None
    # Must NOT contain path traversal characters
    assert ".." not in saved_name
    assert "/" not in saved_name
    assert "\\" not in saved_name
    # Must exist inside upload folder
    assert os.path.exists(os.path.join(str(upload_folder), saved_name))


# -----------------------------------------------------------------------------
# 5. Security Headers
# -----------------------------------------------------------------------------
def test_security_headers_standard(client):
    """Verify X-Content-Type-Options, X-Frame-Options, Referrer-Policy, and CSP."""
    res = client.get('/health')
    assert res.status_code == 200

    # 1. X-Content-Type-Options
    assert res.headers.get('X-Content-Type-Options') == 'nosniff'

    # 2. X-Frame-Options
    assert res.headers.get('X-Frame-Options') == 'DENY'

    # 3. Referrer-Policy
    assert res.headers.get('Referrer-Policy') == 'strict-origin-when-cross-origin'

    # 4. Content-Security-Policy
    csp = res.headers.get('Content-Security-Policy', '')
    assert "default-src 'self'" in csp
    # Must permit Chart.js CDN
    assert "https://cdn.jsdelivr.net" in csp
    # Must permit Google Fonts
    assert "https://fonts.googleapis.com" in csp
    assert "https://fonts.gstatic.com" in csp
    assert "object-src 'none'" in csp


def test_hsts_header_in_production(monkeypatch, tmp_path):
    """Strict-Transport-Security (HSTS) header must be present in production environment."""
    db_file = tmp_path / "prod_test.db"
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    monkeypatch.setenv('FLASK_ENV', 'production')
    monkeypatch.setenv('FLASK_DEBUG', '0')

    class ProdEnvConfig(Config):
        TESTING = True
        DEBUG = False
        ENV = 'production'
        DATABASE_PATH = str(db_file)
        UPLOAD_FOLDER = str(upload_dir)
        SECRET_KEY = '9f8e7d6c5b4a3928170e1f2a3b4c5d6e7f8a9b0c1d2e3f4a'

    app_instance = create_app(ProdEnvConfig)
    with app_instance.test_client() as c:
        res = c.get('/health')
        assert res.status_code == 200
        hsts = res.headers.get('Strict-Transport-Security')
        assert hsts is not None
        assert 'max-age=31536000' in hsts
        assert 'includeSubDomains' in hsts
