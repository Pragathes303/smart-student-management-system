"""
Phase 1 - Security Test Suite
Validates all requirements specified in Phase 1:
1. Password hashing (Werkzeug PBKDF2:SHA-256) & one-time plain-text password migration.
2. Password policy: min 8 characters, cannot be same as username, uppercase, lowercase, digit, special character.
3. Force password change on first login for default/admin accounts (must_change_password enforcement).
4. Login rate limiting / lockout after 5 failed attempts & generic error message ("Invalid credentials").
5. Secure sessions: HTTPOnly, SameSite=Lax, Secure in production, 30-min idle timeout, session reset on login/logout.
6. Forgot/reset password flow (cryptographic signed token with expiry) and admin-initiated temporary reset.
"""

import time
import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from backend.config import Config
from backend.limiter import clear_failed_logins, is_login_locked, record_failed_login
from backend.security import (
    generate_password_reset_token,
    generate_temp_password,
    validate_password_strength,
    verify_password_reset_token,
    verify_password_reset_token_data,
)
from backend.utils import validate_user_data
from database.db import execute_db, query_db
from database.init_db import is_password_hash, migrate_plaintext_passwords


# =============================================================================
# 1. Password Hashing & Plain-text Password Migration
# =============================================================================
def test_plaintext_password_migration(app):
    """Verify that legacy unhashed/plain-text passwords in users table are migrated to PBKDF2:SHA-256."""
    with app.app_context():
        # Insert a user with a raw plain-text password
        execute_db(
            """
            INSERT INTO users (username, password_hash, role, full_name, email, is_active, must_change_password)
            VALUES (?, ?, 'staff', 'Legacy User', 'legacy@test.edu', 1, 0)
            """,
            ("legacy_user", "PlainTextPass@123"),
            commit=True
        )

        user_before = query_db("SELECT password_hash FROM users WHERE username = 'legacy_user'", one=True)
        assert user_before['password_hash'] == "PlainTextPass@123"
        assert not is_password_hash(user_before['password_hash'])

        # Run migration
        migrated_count = migrate_plaintext_passwords()
        assert migrated_count >= 1

        user_after = query_db("SELECT password_hash FROM users WHERE username = 'legacy_user'", one=True)
        assert is_password_hash(user_after['password_hash'])
        assert user_after['password_hash'].startswith('pbkdf2:sha256:')
        assert check_password_hash(user_after['password_hash'], "PlainTextPass@123")

        # Idempotent: running again should migrate 0
        second_run = migrate_plaintext_passwords()
        assert second_run == 0


def test_password_hash_detection_helper():
    """Verify is_password_hash correctly distinguishes hashes from raw strings."""
    assert is_password_hash(generate_password_hash("Secret@123", method="pbkdf2:sha256"))
    assert is_password_hash("pbkdf2:sha256:260000$salt$hashvalue")
    assert is_password_hash("scrypt:32768:8:1$salt$hashvalue")
    assert is_password_hash("$2b$12$e86kDAWqCqP9q8q8q8q8qe86kDAWqCqP9q8q8q8q8q")

    assert not is_password_hash("PlainTextPassword")
    assert not is_password_hash("Admin@123")
    assert not is_password_hash("")
    assert not is_password_hash(None)


# =============================================================================
# 2. Password Policy (Min 8 chars, Not Same as Username, Complexity)
# =============================================================================
def test_password_policy_min_length():
    """Password must be at least 8 characters."""
    valid, msg = validate_password_strength("Ab1!xyz")
    assert valid is False
    assert "at least 8 characters" in msg.lower()

    valid, msg = validate_password_strength("Ab1!wxyz")
    assert valid is True
    assert msg is None


def test_password_policy_not_same_as_username():
    """Password cannot match the username (case-insensitive)."""
    valid, msg = validate_password_strength("SuperAdmin@123", username="SuperAdmin@123")
    assert valid is False
    assert "cannot be the same as your username" in msg.lower()

    valid, msg = validate_password_strength("ADMINISTRATOR", username="administrator")
    assert valid is False
    assert "cannot be the same as your username" in msg.lower()

    # Different from username satisfies policy
    valid, msg = validate_password_strength("StrongPass@2026", username="administrator")
    assert valid is True


def test_password_policy_complexity_elements():
    """Requires uppercase, lowercase, digit, and special character."""
    # Missing uppercase
    assert not validate_password_strength("strongpass@2026")[0]
    # Missing lowercase
    assert not validate_password_strength("STRONGPASS@2026")[0]
    # Missing digit
    assert not validate_password_strength("StrongPass@Spec")[0]
    # Missing special character
    assert not validate_password_strength("StrongPass2026")[0]
    # Complete
    assert validate_password_strength("StrongPass@2026")[0]


def test_validate_user_data_enforces_password_policy(app):
    """User creation validation requires full password policy adherence."""
    with app.app_context():
        # Valid user
        is_valid, errs, cleaned = validate_user_data(
            "newstaff", "ValidPass@123", "staff", "New Staff", "newstaff@test.edu"
        )
        assert is_valid is True
        assert len(errs) == 0

        # Password same as username rejected
        is_valid, errs, _ = validate_user_data(
            "SameUserPass@1", "SameUserPass@1", "staff", "Same User", "same@test.edu"
        )
        assert is_valid is False
        assert any("username" in e.lower() for e in errs)

        # Weak password rejected
        is_valid, errs, _ = validate_user_data(
            "staff2", "weak", "staff", "Staff Two", "staff2@test.edu"
        )
        assert is_valid is False
        assert any("at least 8 characters" in e.lower() for e in errs)


def test_generated_temp_password_guarantees_policy():
    """generate_temp_password must always produce a password satisfying all policy checks."""
    for _ in range(20):
        temp = generate_temp_password(14, username="testuser")
        is_valid, msg = validate_password_strength(temp, username="testuser")
        assert is_valid is True, f"Generated temp password '{temp}' failed policy: {msg}"


# =============================================================================
# 3. Force Password Change on First Login for Default/Admin Accounts
# =============================================================================
def test_forced_password_change_lifecycle(client, app):
    """Users with must_change_password=1 must be redirected to /change-password until updated."""
    with app.app_context():
        temp_pwd = generate_password_hash("Default@123", method="pbkdf2:sha256")
        execute_db(
            """
            INSERT INTO users (username, password_hash, role, full_name, email, is_active, must_change_password, session_version)
            VALUES (?, ?, 'admin', 'Default Admin', 'defaultadmin@test.edu', 1, 1, 1)
            """,
            ("default_admin", temp_pwd),
            commit=True
        )

    # 1. Login with default credentials
    res_login = client.post('/login', data={'username': 'default_admin', 'password': 'Default@123'}, follow_redirects=False)
    assert res_login.status_code == 302
    assert '/change-password' in res_login.headers['Location']

    # 2. Attempting to browse other protected endpoints forces redirect to /change-password
    res_dash = client.get('/dashboard', follow_redirects=False)
    assert res_dash.status_code == 302
    assert '/change-password' in res_dash.headers['Location']

    res_students = client.get('/students', follow_redirects=False)
    assert res_students.status_code == 302
    assert '/change-password' in res_students.headers['Location']

    # 3. Change password successfully with strong password
    res_change = client.post('/change-password', data={
        'current_password': 'Default@123',
        'new_password': 'PersonalSecure@2026',
        'confirm_password': 'PersonalSecure@2026'
    }, follow_redirects=True)
    assert res_change.status_code == 200
    assert b"updated successfully" in res_change.data

    # 4. must_change_password flag is now cleared to 0 in the database
    with app.app_context():
        u = query_db("SELECT must_change_password FROM users WHERE username = 'default_admin'", one=True)
        assert u['must_change_password'] == 0

    # 5. Accessing dashboard is now permitted
    res_dash_after = client.get('/dashboard')
    assert res_dash_after.status_code == 200


# =============================================================================
# 4. Login Rate Limiting / Lockout & Generic Error Message
# =============================================================================
def test_login_lockout_after_five_failed_attempts(client):
    """Account or IP lockout is triggered after 5 consecutive failed login attempts."""
    ip = "192.168.10.50"
    uname = "lockout_target"
    clear_failed_logins(ip, uname)

    for i in range(4):
        record_failed_login(ip, uname)
        locked, _ = is_login_locked(ip, uname)
        assert not locked, f"Should not lock on attempt {i+1}"

    # 5th failure triggers lockout
    record_failed_login(ip, uname)
    locked, retry_after = is_login_locked(ip, uname)
    assert locked is True
    assert retry_after > 0

    # Endpoint returns 429 when locked
    res = client.post(
        '/login',
        data={'username': uname, 'password': 'WrongPassword@1'},
        headers={'X-Forwarded-For': ip}
    )
    assert res.status_code == 429
    assert b"Too many login attempts" in res.data or b"locked" in res.data.lower()

    # Clean up
    clear_failed_logins(ip, uname)


def test_login_generic_error_message(client):
    """Failed login returns generic error message without enumerating username presence."""
    # Non-existent user
    res_nonexistent = client.post('/login', data={'username': 'nobody_here_xyz', 'password': 'WrongPassword@1'}, follow_redirects=True)
    assert b"Invalid credentials" in res_nonexistent.data

    # Existing user with wrong password
    res_wrong_pwd = client.post('/login', data={'username': 'admin', 'password': 'WrongPassword@1'}, follow_redirects=True)
    assert b"Invalid credentials" in res_wrong_pwd.data


# =============================================================================
# 5. Secure Session Cookies, Reset on Login/Logout & 30-Min Idle Timeout
# =============================================================================
def test_secure_session_cookie_flags(app):
    """Verify session cookie security flags."""
    assert app.config['SESSION_COOKIE_HTTPONLY'] is True
    assert app.config['SESSION_COOKIE_SAMESITE'] == 'Lax'
    assert app.config['PERMANENT_SESSION_LIFETIME'] == 1800  # 30 minutes


def test_production_session_cookie_secure(monkeypatch):
    """In production mode, SESSION_COOKIE_SECURE must be True."""
    import importlib
    monkeypatch.setenv('FLASK_ENV', 'production')
    monkeypatch.setenv('SECRET_KEY', 'a' * 32)
    import backend.config as cfg_module
    importlib.reload(cfg_module)
    try:
        assert cfg_module.Config.SESSION_COOKIE_SECURE is True
        assert cfg_module.Config.SESSION_COOKIE_NAME == '__Host-session'
    finally:
        monkeypatch.delenv('FLASK_ENV', raising=False)
        importlib.reload(cfg_module)


def test_session_reset_on_login_and_logout(client):
    """Login and logout assign fresh sessions, invalidating previous state."""
    # Set pre-login cookie / session state
    with client.session_transaction() as sess:
        sess['pre_auth_tracker'] = 'unauthorized_token'

    # Login
    res_login = client.post('/login', data={'username': 'admin', 'password': 'Admin@123'}, follow_redirects=True)
    assert res_login.status_code == 200

    # Ensure pre-auth tracker was wiped out during session reset on login
    with client.session_transaction() as sess:
        assert 'pre_auth_tracker' not in sess
        assert sess.get('username') == 'admin'
        assert sess.get('_last_active') is not None

    # Logout
    res_logout = client.post('/logout', follow_redirects=True)
    assert res_logout.status_code == 200

    with client.session_transaction() as sess:
        assert 'user_id' not in sess
        assert 'username' not in sess


def test_session_idle_timeout_expiration(client):
    """Session expires after 30 minutes (1800s) of inactivity."""
    client.post('/login', data={'username': 'admin', 'password': 'Admin@123'}, follow_redirects=True)

    # Verify initially active
    res_dash = client.get('/dashboard')
    assert res_dash.status_code == 200

    # Fast forward session _last_active by 31 minutes (1860 seconds in the past)
    with client.session_transaction() as sess:
        sess['_last_active'] = time.time() - 1860

    # Next request triggers idle timeout redirect to login
    res_expired = client.get('/dashboard', follow_redirects=False)
    assert res_expired.status_code == 302
    assert '/login' in res_expired.headers['Location']

    # Session is cleared
    with client.session_transaction() as sess:
        assert 'user_id' not in sess


# =============================================================================
# 6. Forgot/Reset Password Token & Admin-Initiated Reset Flow
# =============================================================================
def test_forgot_and_reset_password_flow(client, app):
    """Forgot password request generates signed token; reset enforces complexity and single-use."""
    with app.app_context():
        user = query_db("SELECT id, email, session_version FROM users WHERE username = 'staff'", one=True)
        secret = app.config['SECRET_KEY']

        # 1. Generate token
        token = generate_password_reset_token(user['id'], session_version=user['session_version'], secret_key=secret)
        assert verify_password_reset_token(token, secret_key=secret) == user['id']

        # 2. Reset password successfully
        res_reset = client.post(f'/reset-password/{token}', data={
            'password': 'StaffNewSecure@2026',
            'confirm_password': 'StaffNewSecure@2026'
        }, follow_redirects=True)
        assert res_reset.status_code == 200
        assert b"successfully reset" in res_reset.data

        # 3. Single-use: Reusing the same token must fail because session_version bumped
        res_reuse = client.post(f'/reset-password/{token}', data={
            'password': 'AnotherPassword@123',
            'confirm_password': 'AnotherPassword@123'
        }, follow_redirects=True)
        assert b"already been used" in res_reuse.data.lower() or b"invalid" in res_reuse.data.lower()


def test_admin_initiated_password_reset(admin_client, app):
    """Admin resets a staff member's password, generating a temporary password and setting must_change_password=1."""
    with app.app_context():
        staff = query_db("SELECT id FROM users WHERE username = 'staff'", one=True)
        staff_id = staff['id']

    res = admin_client.post(f'/users/{staff_id}/reset-password', follow_redirects=True)
    assert res.status_code == 200
    assert b"Temporary Password:" in res.data

    with app.app_context():
        updated_staff = query_db("SELECT must_change_password, session_version FROM users WHERE id = ?", (staff_id,), one=True)
        assert updated_staff['must_change_password'] == 1
