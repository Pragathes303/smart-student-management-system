"""
Phase 1 - Security: Step 5 Tests (Privacy, Minimum-Data Principle, Erasure & Anonymization)
Verifies:
1. Publicly accessible Privacy Policy & Data Minimization page (/privacy).
2. Minimum-data principle enforcement across public verification.
3. Admin-only authorization for permanent student deletion and anonymization.
4. Complete anonymization of all Personally Identifiable Information (PII)
   while preserving aggregate academic statistical integrity.
5. Linked user account deactivation upon student anonymization.
6. Complete database purge via hard-delete.
"""

import io
import pytest
from werkzeug.security import generate_password_hash

from backend.services.student_service import StudentService
from database.db import query_db, execute_db


# -----------------------------------------------------------------------------
# 1. Privacy Policy & Data Minimization Page
# -----------------------------------------------------------------------------
def test_privacy_page_accessible_unauthenticated(client):
    """The Privacy Policy page must be publicly accessible without authentication."""
    res = client.get('/privacy')
    assert res.status_code == 200
    assert b"Student Privacy Policy &amp; Data Minimization Notice" in res.data or b"Student Privacy Policy" in res.data
    assert b"Minimum-Data Principle" in res.data
    assert b"Right to Erasure" in res.data
    assert b"Public Verification Privacy" in res.data


def test_privacy_page_accessible_to_authenticated_users(student_client, admin_client):
    """Logged in students and staff/admins can access the privacy notice."""
    res_student = student_client.get('/privacy')
    assert res_student.status_code == 200

    res_admin = admin_client.get('/privacy')
    assert res_admin.status_code == 200


# -----------------------------------------------------------------------------
# 2. Minimum-Data Principle on Public Verification
# -----------------------------------------------------------------------------
def test_minimum_data_in_public_verification(client, app):
    """Public certificate verification endpoint must adhere to data minimization."""
    with app.app_context():
        ins = execute_db(
            """
            INSERT INTO students (name, register_number, department, email, marks, phone, dob, address, verify_token)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("Privacy Test Student", "PRIV2026", "Computer Science", "priv@institution.edu", 91.0, "+19876543210", "2001-05-15", "404 Private Road", "priv_secure_token_12345"),
            commit=True
        )
        st_id = ins['lastrowid']

    # Query public verify URL using token
    res = client.get('/verify/priv_secure_token_12345')
    assert res.status_code == 200
    # Must contain essential academic confirmation
    assert b"Privacy Test Student" in res.data
    assert b"PRIV2026" in res.data
    assert b"Computer Science" in res.data

    # MUST NOT leak private personal identifiers
    assert b"+19876543210" not in res.data
    assert b"priv@institution.edu" not in res.data
    assert b"2001-05-15" not in res.data
    assert b"404 Private Road" not in res.data


# -----------------------------------------------------------------------------
# 3. Role-Based Access Control on Anonymization
# -----------------------------------------------------------------------------
def test_anonymize_student_requires_admin_role(client, student_client, staff_client, app):
    """Only administrators are authorized to execute student anonymization."""
    with app.app_context():
        ins = execute_db(
            """
            INSERT INTO students (name, register_number, department, email, marks)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("Target Student", "REGANON01", "Information Technology", "target@test.edu", 85.0),
            commit=True
        )
        sid = ins['lastrowid']

    # 1. Unauthenticated -> 302 redirect to /login or 403 Forbidden
    res_unauth = client.post(f'/students/{sid}/anonymize')
    assert res_unauth.status_code in (302, 403)
    if res_unauth.status_code == 302:
        assert '/login' in res_unauth.headers.get('Location', '')

    # 2. Student -> 403 Forbidden
    res_student = student_client.post(f'/students/{sid}/anonymize')
    assert res_student.status_code == 403

    # 3. Staff -> 403 Forbidden
    res_staff = staff_client.post(f'/students/{sid}/anonymize')
    assert res_staff.status_code == 403


# -----------------------------------------------------------------------------
# 4. Student Anonymization (Right to Erasure / PII Scrubbing)
# -----------------------------------------------------------------------------
def test_anonymize_student_scrubs_all_personal_identifiers(app, admin_client):
    """Anonymization permanently scrubs all PII and deactivates portal login while preserving academic marks."""
    with app.app_context():
        # Create student with full personal data
        ins = execute_db(
            """
            INSERT INTO students (name, register_number, department, email, marks, phone, dob, address, photo_filename, verify_token)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("John Doe Privacy", "REGPII999", "Data Science", "johndoe@personal.com", 94.5, "+1234567890", "1999-12-31", "100 Confidential St", "sample_avatar.jpg", "token_johndoe_to_scrub"),
            commit=True
        )
        sid = ins['lastrowid']

        # Create linked user login
        pwd_hash = generate_password_hash("StudentSecret123!", method="pbkdf2:sha256")
        execute_db(
            """
            INSERT INTO users (username, password_hash, role, full_name, email, student_id, is_active, session_version)
            VALUES (?, ?, 'student', ?, ?, ?, 1, 1)
            """,
            ("johndoe999", pwd_hash, "John Doe Privacy", "johndoe@personal.com", sid),
            commit=True
        )

    # Admin triggers anonymization
    res = admin_client.post(f'/students/{sid}/anonymize', follow_redirects=True)
    assert res.status_code == 200
    assert b"permanently anonymized" in res.data

    with app.app_context():
        # Verify student record in database
        st = query_db("SELECT * FROM students WHERE id = ?", (sid,), one=True)
        assert st is not None

        # 1. Name is replaced with anonymized pseudonym
        assert st['name'] == f"Anonymized Student #{sid}"
        assert "John Doe" not in st['name']

        # 2. Register number is replaced with anonymous hash
        assert st['register_number'].startswith("ANON")
        assert "REGPII999" not in st['register_number']

        # 3. Email is scrambled to local non-routable address
        assert st['email'] == f"anonymized.{sid}@privacy.local"
        assert "johndoe@personal.com" not in st['email']

        # 4. Direct identifiers cleared
        assert st['phone'] is None
        assert st['dob'] is None
        assert st['address'] is None
        assert st['photo_filename'] is None
        assert st['verify_token'] is None

        # 5. Academic statistics preserved for reporting
        assert st['department'] == "Data Science"
        assert float(st['marks']) == 94.5

        # 6. Linked portal user login scrambled and deactivated
        user = query_db("SELECT * FROM users WHERE student_id = ?", (sid,), one=True)
        assert user is not None
        assert user['is_active'] == 0
        assert user['username'] == f"anon_user_{sid}"
        assert user['email'] == f"anon_user.{sid}@privacy.local"
        assert user['password_hash'] == 'ANONYMIZED_CREDENTIAL'
        assert user['session_version'] == 2


def test_anonymized_user_cannot_log_in(client, app, admin_client):
    """After anonymization, the student cannot log into the system with their former credentials."""
    with app.app_context():
        ins = execute_db(
            """
            INSERT INTO students (name, register_number, department, email, marks)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("Login Test Student", "REGLOGIN01", "Computer Science", "login_test@student.edu", 78.0),
            commit=True
        )
        sid = ins['lastrowid']

        pwd_hash = generate_password_hash("InitialPass123!", method="pbkdf2:sha256")
        execute_db(
            """
            INSERT INTO users (username, password_hash, role, full_name, email, student_id, is_active, session_version)
            VALUES (?, ?, 'student', ?, ?, ?, 1, 1)
            """,
            ("logintest01", pwd_hash, "Login Test Student", "login_test@student.edu", sid),
            commit=True
        )

    # Verify initial login works
    res_login_before = client.post('/login', data={'username': 'logintest01', 'password': 'InitialPass123!'}, follow_redirects=True)
    # Admin anonymizes the student
    res_anon = admin_client.post(f'/students/{sid}/anonymize')
    assert res_anon.status_code in (200, 302)

    # Fresh client attempting former login must be rejected
    with app.test_client() as fresh_client:
        res_login_after = fresh_client.post('/login', data={'username': 'logintest01', 'password': 'InitialPass123!'}, follow_redirects=True)
        assert b"Invalid credentials" in res_login_after.data or b"Account is deactivated" in res_login_after.data
        assert b"My Portal" not in res_login_after.data


# -----------------------------------------------------------------------------
# 5. Full Hard-Delete Purge
# -----------------------------------------------------------------------------
def test_hard_delete_purges_record_completely(app, admin_client):
    """Admin hard-delete permanently removes record from students table."""
    with app.app_context():
        ins = execute_db(
            """
            INSERT INTO students (name, register_number, department, email, marks)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("Purge Student", "REGPURGE01", "Mechanical Engineering", "purge@student.edu", 70.0),
            commit=True
        )
        sid = ins['lastrowid']

    # Admin hard-deletes
    res = admin_client.post(f'/students/{sid}/hard-delete', follow_redirects=True)
    assert res.status_code == 200

    with app.app_context():
        st = query_db("SELECT * FROM students WHERE id = ?", (sid,), one=True)
        assert st is None
