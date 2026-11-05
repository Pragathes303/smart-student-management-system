"""
Security Regression Test Suite (Proving S1, S2, S3, S4, S7)
These tests assert the REQUIRED SECURE BEHAVIOR after Phase 2 remediation.
All tests must now pass cleanly (green).
"""



# -----------------------------------------------------------------------------
# S1: Role-Based Access Control (RBAC) Violations for Student Role
# -----------------------------------------------------------------------------
def test_s1_student_cannot_add_student(student_client):
    """A student must receive 403 Forbidden when attempting to access /students/add."""
    res = student_client.get('/students/add')
    assert res.status_code == 403


def test_s1_student_cannot_edit_other_student(student_client):
    """A student must receive 403 Forbidden when attempting to edit a student."""
    res = student_client.get('/students/1/edit')
    assert res.status_code == 403


def test_s1_student_cannot_export_all_students(student_client):
    """A student must receive 403 Forbidden when accessing /students/export."""
    res = student_client.get('/students/export')
    assert res.status_code == 403


def test_s1_student_cannot_bulk_import_students(student_client):
    """A student must receive 403 Forbidden when accessing /students/import."""
    res = student_client.get('/students/import')
    assert res.status_code == 403


# -----------------------------------------------------------------------------
# S2: Unauthenticated REST API Endpoints
# -----------------------------------------------------------------------------
def test_s2_unauthenticated_api_access_blocked(client):
    """Unauthenticated requests to /api/v1/students must be rejected with 401."""
    res = client.get('/api/v1/students')
    assert res.status_code == 401


def test_s2_unauthenticated_api_stats_blocked(client):
    """Unauthenticated requests to /api/v1/students/stats must be rejected with 401."""
    res = client.get('/api/v1/students/stats')
    assert res.status_code == 401


# -----------------------------------------------------------------------------
# S3: CSRF Protection & POST-Only Logout
# -----------------------------------------------------------------------------
def test_s3_post_request_requires_csrf_protection(app):
    """When CSRF protection is active, POST without CSRF token must return 400."""
    app.config['WTF_CSRF_ENABLED'] = True
    csrf_client = app.test_client()
    res = csrf_client.post('/login', data={'username': 'admin', 'password': 'Admin@123'})
    assert res.status_code == 400


def test_s3_logout_rejects_get_request(admin_client):
    """GET /logout must be rejected with 405 Method Not Allowed; logout must be POST."""
    res = admin_client.get('/logout')
    assert res.status_code == 405


# -----------------------------------------------------------------------------
# S4: Public Verification Data Exposure & Guessable Register Numbers
# -----------------------------------------------------------------------------
def test_s4_public_verify_guessable_reg_number_blocked(client):
    """Accessing /verify/<reg_number> must not expose records; only random verify_tokens allowed."""
    res = client.get('/verify/REG999')
    # Predictable reg_number URLs return 404
    assert res.status_code == 404


# -----------------------------------------------------------------------------
# S7: Open Redirect Vulnerability in Login Flow
# -----------------------------------------------------------------------------
def test_s7_login_rejects_open_redirect(client):
    """Login with protocol-relative URL (e.g. //evil.com) must NOT redirect to //evil.com."""
    res = client.post('/login?next=//evil.com', data={'username': 'admin', 'password': 'Admin@123'}, follow_redirects=False)
    assert res.status_code == 302
    assert not res.headers.get('Location', '').startswith('//evil.com')


# -----------------------------------------------------------------------------
# API CSRF & Auth Hardening Tests
# -----------------------------------------------------------------------------
def test_api_session_cookie_post_without_csrf_rejected_403(admin_client):
    """(a) Mutating session-cookie request without CSRF must be rejected with 403."""
    res = admin_client.post('/api/v1/students', json={
        'name': 'CSRF Reject Student',
        'register_number': 'REG_NOCSRF_1',
        'department': 'Computer Science',
        'email': 'nocsrf@example.edu',
        'marks': 92.0
    })
    assert res.status_code == 403
    assert res.get_json()['error'] == 'Forbidden'


def test_api_bearer_token_post_allowed(app, client):
    """(b) Mutating request with valid Bearer token must succeed without CSRF."""
    with app.app_context():
        import hashlib

        from database.db import execute_db
        raw_token = "valid_api_test_bearer_token_999"
        tok_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
        execute_db(
            "INSERT INTO api_tokens (token_hash, name, role, revoked) VALUES (?, ?, 'admin', 0)",
            (tok_hash, "Bearer Test Admin"),
            commit=True
        )

    res = client.post('/api/v1/students', json={
        'name': 'Bearer Student',
        'register_number': 'REG_BEARER_1',
        'department': 'Computer Science',
        'email': 'bearer@example.edu',
        'marks': 88.0
    }, headers={'Authorization': f'Bearer {raw_token}'})
    assert res.status_code == 201
    assert 'id' in res.get_json()


def test_api_session_cookie_get_allowed(admin_client):
    """(c) Non-mutating GET request with session cookie must succeed."""
    res = admin_client.get('/api/v1/students')
    assert res.status_code == 200
    assert 'students' in res.get_json()


# -----------------------------------------------------------------------------
# Rate Limiting Hardening Tests (S10)
# -----------------------------------------------------------------------------
def test_forgot_password_rate_limit_exceeded(app, client):
    """POST /forgot-password returns 429 when rate limit threshold is exceeded."""
    app.config['TEST_RATE_LIMITER'] = True
    try:
        for _ in range(5):
            res = client.post('/forgot-password', data={'email': 'student@test.edu'})
            assert res.status_code in (200, 302)
        res_blocked = client.post('/forgot-password', data={'email': 'student@test.edu'})
        assert res_blocked.status_code == 429
    finally:
        app.config['TEST_RATE_LIMITER'] = False


def test_reset_password_rate_limit_exceeded(app, client):
    """POST /reset-password/<token> returns 429 when rate limit threshold is exceeded."""
    app.config['TEST_RATE_LIMITER'] = True
    try:
        for _ in range(10):
            res = client.post('/reset-password/dummy-token', data={'password': 'NewPassword123!', 'confirm_password': 'NewPassword123!'})
            assert res.status_code in (200, 302)
        res_blocked = client.post('/reset-password/dummy-token', data={'password': 'NewPassword123!', 'confirm_password': 'NewPassword123!'})
        assert res_blocked.status_code == 429
    finally:
        app.config['TEST_RATE_LIMITER'] = False


def test_api_v1_rate_limit_exceeded(app, client):
    """Requests to /api/v1/* return 429 when default rate limit is exceeded."""
    app.config['TEST_RATE_LIMITER'] = True
    try:
        blocked = False
        for _ in range(65):
            res = client.get('/api/v1/tokens')
            if res.status_code == 429:
                blocked = True
                break
        assert blocked
    finally:
        app.config['TEST_RATE_LIMITER'] = False


# -----------------------------------------------------------------------------
# Code Gap Hardening Tests (CSV Sanitization & Security Headers)
# -----------------------------------------------------------------------------
def test_sanitize_csv_cell_strips_null_bytes():
    """Verify sanitize_csv_cell strips null bytes before prefixing."""
    from backend.security import sanitize_csv_cell
    assert sanitize_csv_cell("\x00=1+1") == "'=1+1"
    assert sanitize_csv_cell("=1+1\x00") == "'=1+1"
    assert sanitize_csv_cell("normal\x00text") == "normaltext"
    assert sanitize_csv_cell(None) == ""


def test_x_xss_protection_header_present(client):
    """Verify X-XSS-Protection header is present on responses."""
    res = client.get('/health')
    assert res.headers.get('X-XSS-Protection') == '1; mode=block'
