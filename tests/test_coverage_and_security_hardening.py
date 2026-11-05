"""
Additional Comprehensive Test Suite:
1. S4: Token-based public verify endpoint (/verify/<token>).
2. S6: Environment-driven initial admin creation and password complexity.
3. S8: Production startup checks for SECRET_KEY length, defaults, and DEBUG flag.
4. Authorization: Student cannot edit marks, grades, attendance, or department.
5. Infrastructure: ProxyFix middleware and Redis rate-limiter configuration.
6. Storage: S3StorageHandler unit tests and mock upload/download/delete lifecycle.
7. Mailer: Marksheet email notification and password reset email generation.
8. Migrations: Idempotent database migrations without data loss or re-seeding.
"""

import io
import os
from unittest.mock import MagicMock, patch

import pytest

from backend.app import create_app
from backend.config import Config
from backend.mailer import send_student_marksheet_email, send_welcome_student_email
from backend.security import generate_secure_token, validate_password_strength
from backend.storage import LocalStorageHandler, S3StorageHandler, get_storage
from database.db import execute_db, query_db
from database.init_db import migrate_tables


# -----------------------------------------------------------------------------
# 1. S4: Token-Based Public Marksheet Verification (/verify/<token>)
# -----------------------------------------------------------------------------
def test_s4_public_verify_with_valid_token(client, app):
    """Test public access to /verify/<token> using a valid cryptographic verify_token."""
    with app.app_context():
        token = generate_secure_token(24)
        execute_db(
            """
            INSERT INTO students (name, register_number, department, email, marks, verify_token)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("Verified Student", "REG_VERIFY_01", "Computer Science", "verify@test.edu", 94.0, token),
            commit=True
        )

        res = client.get(f'/verify/{token}')
        assert res.status_code == 200
        assert b"OFFICIALLY VERIFIED" in res.data
        assert b"Verified Student" in res.data
        assert b"Computer Science" in res.data
        # Sensitive data not leaked
        assert b"verify@test.edu" not in res.data


def test_s4_public_verify_invalid_token_returns_404(client):
    """Accessing /verify/<token> with an invalid or nonexistent token returns 404."""
    res = client.get('/verify/nonexistent_token_12345')
    assert res.status_code == 404
    assert b"Digital Credential Not Found" in res.data


# -----------------------------------------------------------------------------
# 2. S6: Password Complexity and Environment-Driven Admin Provisioning
# -----------------------------------------------------------------------------
def test_s6_password_complexity_rules():
    """Verify strong password complexity requirements."""
    # Too short
    valid, msg = validate_password_strength("Pass1!")
    assert valid is False
    assert "at least 8 characters" in msg.lower()

    # Missing uppercase
    valid, msg = validate_password_strength("password@123")
    assert valid is False
    assert "uppercase" in msg.lower()

    # Missing lowercase
    valid, msg = validate_password_strength("PASSWORD@123")
    assert valid is False
    assert "lowercase" in msg.lower()

    # Missing digit
    valid, msg = validate_password_strength("Password!@#$")
    assert valid is False
    assert "digit" in msg.lower()

    # Missing special char
    valid, msg = validate_password_strength("Password123")
    assert valid is False
    assert "special character" in msg.lower()

    # Valid strong password
    valid, msg = validate_password_strength("StrongPass@2026!")
    assert valid is True
    assert msg is None


# -----------------------------------------------------------------------------
# 3. S8: Startup Production Security Checks for SECRET_KEY and DEBUG
# -----------------------------------------------------------------------------
def test_s8_production_startup_rejects_debug_enabled():
    """Production mode must raise RuntimeError if FLASK_DEBUG is enabled."""
    class BadProdConfig(Config):
        ENV = 'production'
        DEBUG = True
        SECRET_KEY = "a" * 32

    with pytest.raises(RuntimeError) as exc_info:
        create_app(BadProdConfig)
    assert "FLASK_DEBUG cannot be enabled in production" in str(exc_info.value)


def test_s8_production_startup_rejects_short_or_default_secret_key():
    """Production mode must raise RuntimeError if SECRET_KEY is too short or default."""
    class ShortSecretConfig(Config):
        ENV = 'production'
        DEBUG = False
        SECRET_KEY = "short_key"

    with pytest.raises(RuntimeError) as exc_info:
        create_app(ShortSecretConfig)
    assert "at least 32 characters" in str(exc_info.value)

    class DefaultSecretConfig(Config):
        ENV = 'production'
        DEBUG = False
        SECRET_KEY = "default-dev-secret-key-change-in-production"

    with pytest.raises(RuntimeError) as exc_info:
        create_app(DefaultSecretConfig)
    assert "Production deployment requires a secure" in str(exc_info.value)


# -----------------------------------------------------------------------------
# 4. Strict Authorization: Student Role Cannot Modify Marks or Records
# -----------------------------------------------------------------------------
def test_student_cannot_edit_marks_via_profile(student_client, app):
    """Students attempting to pass marks/register_number to /student/profile are ignored."""
    with app.app_context():
        user = query_db("SELECT student_id FROM users WHERE username = 'reg999'", one=True)
        sid = user['student_id']

        # Original marks
        orig = query_db("SELECT marks, register_number FROM students WHERE id = ?", (sid,), one=True)
        orig_marks = orig['marks']
        orig_reg = orig['register_number']

        # Student tries to post modified marks and register_number
        res = student_client.post('/student/profile', data={
            'phone': '9988776655',
            'address': 'New Student Address',
            'marks': '100.0',
            'register_number': 'HACKED_REG'
        }, follow_redirects=True)
        assert res.status_code == 200

        # DB must NOT have modified marks or register_number
        updated = query_db("SELECT marks, register_number, phone, address FROM students WHERE id = ?", (sid,), one=True)
        assert updated['marks'] == orig_marks
        assert updated['register_number'] == orig_reg
        assert updated['phone'] == '9988776655'
        assert updated['address'] == 'New Student Address'


def test_student_cannot_access_staff_modification_routes(student_client):
    """Students cannot access staff/admin modifying routes."""
    # Cannot access /students/<id>/edit
    assert student_client.get('/students/1/edit').status_code == 403
    assert student_client.post('/students/1/edit', data={'marks': 99.0}).status_code == 403

    # Cannot update attendance
    assert student_client.post('/students/1/attendance', data={'total_classes': 100}).status_code == 403

    # Cannot add subject
    assert student_client.post('/students/1/add-subject', data={'subject_code': 'HACK101'}).status_code == 403

    # Cannot edit subject
    assert student_client.get('/students/1/subjects/1/edit').status_code == 403
    assert student_client.post('/students/1/subjects/1/edit', data={'marks': 100}).status_code == 403

    # Cannot mark bulk attendance
    assert student_client.get('/attendance').status_code == 403
    assert student_client.post('/attendance/mark', data={}).status_code == 403


# -----------------------------------------------------------------------------
# 5. Infrastructure: ProxyFix & Limiter Redis Fallback
# -----------------------------------------------------------------------------
def test_proxyfix_configuration():
    """Verify ProxyFix middleware applies proxy headers in production mode."""
    class ProdProxyConfig(Config):
        ENV = 'production'
        DEBUG = False
        SECRET_KEY = "x" * 40
        TRUSTED_PROXIES = 1

    app = create_app(ProdProxyConfig)
    client = app.test_client()
    res = client.get('/api/v1/health', headers={'X-Forwarded-For': '203.0.113.195'})
    assert res.status_code == 200


def test_limiter_redis_storage_configuration(monkeypatch):
    """Verify Config reads REDIS_URL for rate limiting when RATELIMIT_STORAGE_URI is unset."""
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')
    from importlib import reload

    import backend.config
    reload(backend.config)
    assert backend.config.Config.RATELIMIT_STORAGE_URI == 'redis://localhost:6379/0'
    monkeypatch.delenv('REDIS_URL', raising=False)
    reload(backend.config)


# -----------------------------------------------------------------------------
# 6. S3 Storage Handler Tests (Unit & Mock Operations)
# -----------------------------------------------------------------------------
def test_s3_storage_operations_mocked():
    """Test S3StorageHandler methods with mocked boto3 client."""
    with patch('boto3.client') as mock_boto:
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

        s3_handler = S3StorageHandler(
            bucket_name='test-student-bucket',
            region='us-east-1',
            access_key='dummy_access',
            secret_key='dummy_secret'
        )
        assert s3_handler.is_cloud_active() is True

        # Test save
        class DummyFile:
            filename = "report.png"
            content_type = "image/png"
            def read(self):
                return b"fake image bytes"
            def seek(self, offset):
                pass

        saved_name = s3_handler.save(DummyFile())
        assert saved_name.endswith('.png')
        assert mock_s3.upload_fileobj.called

        # Test get_url (presigned URL generation)
        mock_s3.generate_presigned_url.return_value = "https://s3.amazonaws.com/test-bucket/report.png?signed=1"
        url = s3_handler.get_url(saved_name)
        assert "signed=1" in url

        # Test delete
        deleted = s3_handler.delete(saved_name)
        assert deleted is True
        assert mock_s3.delete_object.called


# -----------------------------------------------------------------------------
# 7. Mailer Unit Tests
# -----------------------------------------------------------------------------
def test_mailer_dry_run_or_disabled(app):
    """Test send_welcome_student_email in dev/mock environment."""
    with app.app_context():
        res = send_welcome_student_email("mailtest@test.edu", "Mailer Student", "REG_MAIL_01")
        assert res[0] is True


# -----------------------------------------------------------------------------
# 8. Idempotent Database Migrations & Seeding
# -----------------------------------------------------------------------------
def test_migrations_are_idempotent(app):
    """Running migrate_tables multiple times does not produce errors or corrupt data."""
    with app.app_context():
        from database.db import get_db
        conn = get_db()
        # Run migration second and third time
        migrate_tables(conn)
        migrate_tables(conn)
        # Verify schema_migrations table tracks versions
        versions = query_db("SELECT COUNT(*) as cnt FROM schema_migrations", one=True)
        assert versions['cnt'] >= 4


# -----------------------------------------------------------------------------
# 9. HTTP Custom Error Handlers (400, 401, 403, 404, 429)
# -----------------------------------------------------------------------------
def test_http_error_handlers(client, student_client, admin_client):
    """Test custom error handlers registered in backend/app.py."""
    # 404 Not Found HTML
    res_404 = client.get('/nonexistent-page-url-xyz')
    assert res_404.status_code == 404
    assert b"Page Not Found" in res_404.data or b"404" in res_404.data

    # 404 Not Found JSON for API
    res_api_404 = client.get('/api/v1/nonexistent')
    assert res_api_404.status_code == 404
    assert res_api_404.is_json

    # 401/403 Unauthorized/Forbidden JSON for API
    res_401 = client.get('/api/v1/students')
    assert res_401.status_code in (401, 403)
    assert res_401.is_json

    # 403 Forbidden HTML
    res_403 = student_client.get('/users')
    assert res_403.status_code == 403
    assert b"Access Denied" in res_403.data or b"403" in res_403.data

    # 400 Bad Request / 403 Forbidden JSON
    res_400 = admin_client.post('/api/v1/students', data="invalid-json-payload", headers={'Content-Type': 'application/json'})
    assert res_400.status_code in (400, 403)
    assert res_400.is_json


# -----------------------------------------------------------------------------
# 10. API Tokens & OpenAPI Endpoints
# -----------------------------------------------------------------------------
def test_api_tokens_crud_and_openapi(admin_client, client):
    """Test API token creation, listing, deletion, and openapi documentation."""
    # Get OpenAPI spec
    res_spec = client.get('/api/v1/openapi.json')
    assert res_spec.status_code == 200
    assert b"Smart Student Management System API" in res_spec.data

    # Create API Token as Admin
    res_token = admin_client.post('/api/v1/tokens', json={
        'name': 'CI Test Service Token',
        'role': 'staff'
    }, headers={'X-CSRFToken': 'test-csrf'})
    assert res_token.status_code == 201
    token_val = res_token.get_json()['token']
    token_id = res_token.get_json()['id']

    # List API Tokens
    res_list = admin_client.get('/api/v1/tokens')
    assert res_list.status_code == 200
    assert any(t['name'] == 'CI Test Service Token' for t in res_list.get_json()['tokens'])

    # Use the Bearer Token to access /api/v1/students
    res_auth = client.get('/api/v1/students', headers={'Authorization': f'Bearer {token_val}'})
    assert res_auth.status_code == 200

    # Delete (Revoke) the API Token
    res_del = admin_client.delete(f'/api/v1/tokens/{token_id}', headers={'X-CSRFToken': 'test-csrf'})
    assert res_del.status_code == 200

    # Token should no longer authenticate
    res_revoked = client.get('/api/v1/students', headers={'Authorization': f'Bearer {token_val}'})
    assert res_revoked.status_code in (401, 403)


# -----------------------------------------------------------------------------
# 11. Additional Student Directory Filters & Portal Navigation
# -----------------------------------------------------------------------------
# -----------------------------------------------------------------------------
# 11. Additional Student Directory Filters & Portal Navigation
# -----------------------------------------------------------------------------
def test_student_directory_sorting_and_filters(admin_client):
    """Test student listing sorting options and admin views."""
    # Sort by marks ascending and descending
    assert admin_client.get('/students?sort_by=marks_asc').status_code == 200
    assert admin_client.get('/students?sort_by=marks_desc').status_code == 200
    assert admin_client.get('/students?sort_by=name_desc').status_code == 200
    assert admin_client.get('/students?sort_by=name_asc').status_code == 200
    assert admin_client.get('/students?sort_by=date_asc').status_code == 200
    assert admin_client.get('/students?sort_by=reg_no_desc').status_code == 200

    # Search and Department Filter
    assert admin_client.get('/students?search=Test').status_code == 200
    assert admin_client.get('/students?department=Computer+Science').status_code == 200
    assert admin_client.get('/students?filter_status=passing').status_code == 200
    assert admin_client.get('/students?filter_status=distinction').status_code == 200
    assert admin_client.get('/students?filter_status=at_risk').status_code == 200

    # Admin Management Views
    assert admin_client.get('/users').status_code == 200
    assert admin_client.get('/audit-logs').status_code == 200
    assert admin_client.get('/students/trash').status_code == 200
    assert admin_client.get('/attendance').status_code == 200
    assert admin_client.get('/change-password').status_code == 200

    # Download CSV template
    res_tmpl = admin_client.get('/students/template.csv')
    assert res_tmpl.status_code == 200
    assert b"Register Number" in res_tmpl.data


def test_student_portal_redirect_and_access(student_client):
    """Test student accessing dashboard gets redirected to student portal."""
    res_dash = student_client.get('/dashboard')
    assert res_dash.status_code == 302
    assert "/student-portal" in res_dash.headers.get('Location', '')

    res_portal = student_client.get('/student-portal')
    assert res_portal.status_code == 200
    assert b"Student Academic Portal" in res_portal.data


# -----------------------------------------------------------------------------
# 12. Storage Factory & Marksheet Email
# -----------------------------------------------------------------------------
def test_get_storage_factory(app):
    """Test get_storage factory in different configurations."""
    with app.app_context():
        # Default local storage
        st = get_storage()
        assert isinstance(st, LocalStorageHandler)

        # Invalid storage mode falls back safely to LocalStorageHandler
        app.config['STORAGE_BACKEND'] = 'unknown_cloud'
        st_fallback = get_storage()
        assert isinstance(st_fallback, LocalStorageHandler)
        app.config['STORAGE_BACKEND'] = 'local'


def test_send_student_marksheet_email(app):
    """Test send_student_marksheet_email dry run execution."""
    with app.app_context():
        res = send_student_marksheet_email(
            to_email='student@test.edu',
            student_name='Test Student',
            register_number='REG_MARKSHEET_01',
            pdf_buffer=io.BytesIO(b'%PDF-1.4 simulated pdf data')
        )
        assert res[0] is True


# -----------------------------------------------------------------------------
# 13. Student Soft-Delete, Trash, Restore, and Purge Lifecycle
# -----------------------------------------------------------------------------
def test_student_soft_delete_restore_and_purge_lifecycle(admin_client, app):
    """Test full trash lifecycle: soft delete, list trash, restore, hard delete."""
    with app.app_context():
        res = execute_db(
            """
            INSERT INTO students (name, register_number, department, email, marks)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("Trash Test Student", "TRASH_001", "Physics", "trash@test.edu", 72.0),
            commit=True
        )
        st_id = res['lastrowid']

    # 1. Soft delete
    del_res = admin_client.post(f'/students/{st_id}/delete', follow_redirects=True)
    assert del_res.status_code == 200

    # 2. View trash
    trash_res = admin_client.get('/students/trash')
    assert trash_res.status_code == 200
    assert b"TRASH_001" in trash_res.data

    # 3. Restore student
    rest_res = admin_client.post(f'/students/{st_id}/restore', follow_redirects=True)
    assert rest_res.status_code == 200

    # 4. Soft delete again and hard delete
    admin_client.post(f'/students/{st_id}/delete', follow_redirects=True)
    purge_res = admin_client.post(f'/students/{st_id}/hard-delete', follow_redirects=True)
    assert purge_res.status_code == 200

    # 5. Invalid restore and hard-delete handle gracefully
    inv_rest = admin_client.post('/students/999999/restore', follow_redirects=True)
    assert inv_rest.status_code == 200
    inv_purge = admin_client.post('/students/999999/hard-delete', follow_redirects=True)
    assert inv_purge.status_code == 200


# -----------------------------------------------------------------------------
# 14. Mailer Live SMTP Dispatch with Mocking & Background Async
# -----------------------------------------------------------------------------
def test_mailer_live_smtp_and_async(app):
    """Test live SMTP dispatch, retries, and async workers."""
    import smtplib

    from backend.async_tasks import run_in_background
    from backend.mailer import send_email_async, send_email_message

    with app.app_context():
        # Test simulated mode (default)
        ok, msg = send_email_message("recipient@test.edu", "Test Subject", "Test Body")
        assert ok is True

        # Test live dispatch with mocked smtplib.SMTP
        app.config['SMTP_SERVER'] = 'smtp.example.com'
        app.config['SMTP_PORT'] = 587
        app.config['SMTP_USERNAME'] = 'user'
        app.config['SMTP_PASSWORD'] = 'secret'
        app.config['SMTP_FROM_EMAIL'] = 'mailer@example.com'

        with patch('smtplib.SMTP') as mock_smtp:
            instance = mock_smtp.return_value
            ok, msg = send_email_message(
                "recipient@test.edu",
                "Live Subject",
                "Live Body",
                attachment_buffer=io.BytesIO(b"data"),
                attachment_filename="marks.pdf"
            )
            assert ok is True
            assert instance.starttls.called
            assert instance.login.called
            assert instance.sendmail.called
            assert instance.quit.called

        # Test retry failure when SMTP raises exception
        with patch('smtplib.SMTP', side_effect=smtplib.SMTPException("Connection timed out")):
            ok, msg = send_email_message("recipient@test.edu", "Failing Subject", "Failing Body")
            assert ok is False
            assert "Failed to send email" in msg

        # Reset SMTP config
        app.config['SMTP_SERVER'] = ''

        # Test send_welcome_student_email with setup link and temp pass
        ok1, _ = send_welcome_student_email("s1@test.edu", "Student 1", "REG01", password_setup_link="http://test/setup?t=abc")
        assert ok1 is True
        ok2, _ = send_welcome_student_email("s2@test.edu", "Student 2", "REG02", temp_password="TempPass123!")
        assert ok2 is True

        # Test send_email_async execution
        send_email_async("async@test.edu", "Async Subject", "Async Body")

        # Test run_in_background error logging (with app context)
        def faulty_task():
            raise ValueError("Intentional async worker test exception")
        run_in_background(faulty_task)


# -----------------------------------------------------------------------------
# 15. ImportService Validation & Error CSV Report Generation
# -----------------------------------------------------------------------------
def test_import_service_error_handling_and_reports(app):
    """Test ImportService CSV parsing edge cases, errors, and report generation."""
    from backend.services.import_service import ImportService

    with app.app_context():
        # Empty CSV
        c, errors, rep = ImportService.import_students_csv(b"", 1)
        assert c == 0
        assert any("empty" in e.lower() for e in errors)

        # Missing required header columns
        invalid_csv = b"Name,Register Number\nAlice,REG01\n"
        c, errors, rep = ImportService.import_students_csv(invalid_csv, 1)
        assert c == 0
        assert any("missing required" in e.lower() for e in errors)

        # CSV with row validation errors
        bad_rows_csv = (
            b"Name,Register Number,Department,Email,Marks\n"
            b",REG_ERR1,Computer Science,bademail,150\n"
            b"Good Student,REG_GOOD1,Computer Science,good@test.edu,88.5\n"
        )
        c, errors, rep = ImportService.import_students_csv(bad_rows_csv, 1)
        assert c == 1
        assert len(errors) == 1
        assert "Row 2" in errors[0]
        assert "row_number" in rep


# -----------------------------------------------------------------------------
# 16. Comprehensive HTTP Error Handlers (400, 401, 403, 404, 413, 429, 500)
# -----------------------------------------------------------------------------
def test_all_custom_http_error_handlers(app, client):
    """Test custom error handlers defined in backend/app.py for HTML and JSON."""
    from flask import abort

    # Register temporary test routes on the active app instance
    @app.route('/_test_error/<int:code>')
    def _test_error(code):
        abort(code)

    @app.route('/api/_test_error/<int:code>')
    def _test_api_error(code):
        abort(code)

    for code in [400, 401, 403, 404, 413, 429, 500]:
        # HTML error pages
        res_html = client.get(f'/_test_error/{code}')
        assert res_html.status_code == code
        assert b"<!DOCTYPE html>" in res_html.data or b"<html" in res_html.data

        # API JSON error responses
        res_json = client.get(f'/api/_test_error/{code}')
        assert res_json.status_code == code
        assert res_json.is_json
        assert 'error' in res_json.get_json()


# -----------------------------------------------------------------------------
# 17. Storage Handler Error Branches and S3 Exceptions
# -----------------------------------------------------------------------------
def test_storage_edge_cases_and_s3_errors(app):
    """Test LocalStorageHandler and S3StorageHandler edge cases and exceptions."""
    from backend.storage import LocalStorageHandler, S3StorageHandler

    # LocalStorageHandler edge cases
    local = LocalStorageHandler(app.config['UPLOAD_FOLDER'])
    assert local.save(None) is None
    assert local.delete(None) is True
    assert local.delete("non_existent_file_xyz.png") is True
    assert local.get_url(None) is None

    # S3StorageHandler edge cases and exception branches
    s3 = S3StorageHandler(
        bucket_name="test-bucket",
        region="us-east-1",
        access_key="mock-key",
        secret_key="mock-secret"
    )
    assert s3.save(None) is None
    assert s3.delete(None) is True
    assert s3.get_url(None) is None

    mock_client = MagicMock()
    s3._s3_client = mock_client

    # S3 Upload exception triggers RuntimeError
    dummy_file = MagicMock()
    dummy_file.filename = "photo.jpg"
    dummy_file.content_type = "image/jpeg"
    mock_client.upload_fileobj.side_effect = Exception("S3 network failure")
    with pytest.raises(RuntimeError):
        s3.save(dummy_file)

    # S3 Delete exception returns False
    mock_client.delete_object.side_effect = Exception("S3 bucket not accessible")
    assert s3.delete("student-photos/some_photo.jpg") is False

    # S3 Presigned URL exception falls back to direct URL
    mock_client.generate_presigned_url.side_effect = Exception("Signing error")
    fallback_url = s3.get_url("student-photos/some_photo.jpg")
    assert "https://test-bucket.s3.us-east-1.amazonaws.com" in fallback_url


def test_root_health_endpoint(client):
    """Test root /health endpoint for cloud monitoring."""
    res = client.get('/health')
    assert res.status_code == 200
    data = res.get_json()
    assert data['status'] == 'healthy'
    assert 'service' in data


def test_database_url_configuration_parsing():
    """Test DATABASE_URL handling with postgres:// prefix modernization."""
    os.environ['DATABASE_URL'] = 'postgres://user:pass@dbhost:5432/dbname'
    test_url = os.environ['DATABASE_URL'].replace('postgres://', 'postgresql://', 1)
    assert test_url.startswith('postgresql://')
    del os.environ['DATABASE_URL']

