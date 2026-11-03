"""
Security Regression Test Suite (Proving S1, S2, S3, S4, S7)
These tests assert the REQUIRED SECURE BEHAVIOR after Phase 2 remediation.
All tests must now pass cleanly (green).
"""

import pytest


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
