"""
Phase 1 - Security: Step 2 Authorization & IDOR Tests
Validates:
1. @login_required, @admin_required, and @staff_required decorators on all application routes.
2. Role enforcement:
   - Admin routes strictly allow 'admin' and reject 'staff' and 'student' with HTTP 403 Forbidden.
   - Staff routes allow 'admin' and 'staff' and reject 'student' with HTTP 403 Forbidden.
3. Server-side Student Ownership Verification:
   - Students can only view their own record (/students/<id>), PDF (/students/<id>/pdf),
     and email dispatch (/students/<id>/email-marksheet).
   - Any attempt by a student to access another student's record returns HTTP 403 Forbidden.
   - Students cannot access the global directory (/students) - returns HTTP 403 Forbidden.
4. IDOR Protection on Sub-Resources:
   - Cross-student subject manipulation (/students/<sid1>/subjects/<sid2_sub_id>/edit) returns 403.
   - Cross-student subject deletion (/students/<sid1>/delete-subject/<sid2_sub_id>) returns 403.
"""

import ast
import pytest
from database.db import execute_db, query_db
from werkzeug.security import generate_password_hash


def test_admin_required_blocks_staff(staff_client):
    """Staff role must be rejected with HTTP 403 Forbidden on all admin-only routes."""
    admin_routes_get = [
        '/users',
        '/audit-logs',
        '/audit-logs/export',
        '/students/trash',
    ]
    for url in admin_routes_get:
        res = staff_client.get(url)
        assert res.status_code == 403, f"Expected 403 for staff on GET {url}, got {res.status_code}"
        assert b"Access Denied" in res.data or b"Forbidden" in res.data

    admin_routes_post = [
        ('/students/1/restore', {}),
        ('/students/1/hard-delete', {}),
        ('/settings/semester-lock', {}),
        ('/users/1/toggle-status', {}),
        ('/users/1/reset-password', {}),
        ('/students/1/delete', {}),
    ]
    for url, data in admin_routes_post:
        res = staff_client.post(url, data=data)
        assert res.status_code == 403, f"Expected 403 for staff on POST {url}, got {res.status_code}"


def test_admin_required_blocks_student(student_client):
    """Student role must be rejected with HTTP 403 Forbidden on all admin-only routes."""
    admin_routes_get = [
        '/users',
        '/audit-logs',
        '/audit-logs/export',
        '/students/trash',
    ]
    for url in admin_routes_get:
        res = student_client.get(url)
        assert res.status_code == 403, f"Expected 403 for student on GET {url}, got {res.status_code}"

    admin_routes_post = [
        ('/students/1/restore', {}),
        ('/students/1/hard-delete', {}),
        ('/settings/semester-lock', {}),
        ('/users/1/toggle-status', {}),
        ('/users/1/reset-password', {}),
        ('/students/1/delete', {}),
    ]
    for url, data in admin_routes_post:
        res = student_client.post(url, data=data)
        assert res.status_code == 403, f"Expected 403 for student on POST {url}, got {res.status_code}"


def test_admin_required_allows_admin(admin_client):
    """Admin role must successfully access all admin-designated routes."""
    res_users = admin_client.get('/users')
    assert res_users.status_code == 200

    res_logs = admin_client.get('/audit-logs')
    assert res_logs.status_code == 200

    res_trash = admin_client.get('/students/trash')
    assert res_trash.status_code == 200

    res_export = admin_client.get('/audit-logs/export')
    assert res_export.status_code == 200
    assert res_export.headers.get('Content-Disposition') is not None


def test_staff_required_blocks_student(student_client):
    """Student role must be rejected with HTTP 403 Forbidden on all staff routes."""
    staff_routes_get = [
        '/students',
        '/students/add',
        '/students/1/edit',
        '/students/export',
        '/students/export-excel',
        '/students/template.csv',
        '/students/import',
        '/attendance',
        '/students/1/subjects/1/edit',
    ]
    for url in staff_routes_get:
        res = student_client.get(url)
        assert res.status_code == 403, f"Expected 403 for student on GET {url}, got {res.status_code}"
        assert b"Access Denied" in res.data or b"Forbidden" in res.data

    staff_routes_post = [
        ('/students/add', {'name': 'Unauthorized'}),
        ('/students/1/edit', {'name': 'Unauthorized'}),
        ('/students/1/attendance', {'total_classes': 100, 'attended_classes': 90}),
        ('/students/1/add-subject', {'subject_code': 'HACK', 'subject_name': 'Hack', 'credits': 3, 'marks': 90}),
        ('/students/1/delete-subject/1', {}),
        ('/students/1/subjects/1/edit', {'marks': 100}),
        ('/attendance/mark', {'department': 'Computer Science'}),
    ]
    for url, data in staff_routes_post:
        res = student_client.post(url, data=data)
        assert res.status_code == 403, f"Expected 403 for student on POST {url}, got {res.status_code}"


def test_staff_required_allows_staff_and_admin(staff_client, admin_client):
    """Both staff and admin roles must be allowed on staff-level routes."""
    for cl in [staff_client, admin_client]:
        res_list = cl.get('/students')
        assert res_list.status_code == 200

        res_add = cl.get('/students/add')
        assert res_add.status_code == 200

        res_edit = cl.get('/students/1/edit')
        assert res_edit.status_code == 200

        res_export = cl.get('/students/export')
        assert res_export.status_code == 200

        res_att = cl.get('/attendance')
        assert res_att.status_code == 200


def test_student_ownership_and_idor_protection(app, student_client, client):
    """
    Test student ownership enforcement and IDOR protection:
    - Student 1 can view own record, pdf, email.
    - Student 1 CANNOT view student 2's record, pdf, or email (returns 403).
    """
    with app.app_context():
        # Seed second student
        ins = execute_db(
            """
            INSERT INTO students (name, register_number, department, email, marks)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("Second Student", "REG002", "Information Technology", "second@test.edu", 78.0),
            commit=True
        )
        student2_id = ins['lastrowid']

        stud2_pwd = generate_password_hash("Student2@123", method="pbkdf2:sha256")
        execute_db(
            """
            INSERT INTO users (username, password_hash, role, full_name, email, student_id)
            VALUES (?, ?, 'student', ?, ?, ?)
            """,
            ("reg002", stud2_pwd, "Second Student", "second@test.edu", student2_id),
            commit=True
        )

    # 1. Student 1 accesses own record (ID: 1) -> 200 OK
    res_own = student_client.get('/students/1')
    assert res_own.status_code == 200
    assert b"Test Student" in res_own.data

    # 2. Student 1 accesses Student 2's record (ID: student2_id) -> 403 Forbidden
    res_other = student_client.get(f'/students/{student2_id}')
    assert res_other.status_code == 403
    assert b"Access Denied" in res_other.data

    # 3. Student 1 downloads own PDF -> 200 OK
    res_own_pdf = student_client.get('/students/1/pdf')
    assert res_own_pdf.status_code == 200
    assert res_own_pdf.headers.get('Content-Type') == 'application/pdf'

    # 4. Student 1 tries to download Student 2's PDF -> 403 Forbidden
    res_other_pdf = student_client.get(f'/students/{student2_id}/pdf')
    assert res_other_pdf.status_code == 403
    assert b"Access Denied" in res_other_pdf.data

    # 5. Student 1 emails own marksheet -> 302 Redirect to portal (success)
    res_own_email = student_client.post('/students/1/email-marksheet')
    assert res_own_email.status_code == 302
    assert "/student-portal" in res_own_email.headers.get('Location', '')

    # 6. Student 1 tries to email Student 2's marksheet -> 403 Forbidden
    res_other_email = student_client.post(f'/students/{student2_id}/email-marksheet')
    assert res_other_email.status_code == 403
    assert b"Access Denied" in res_other_email.data


def test_cross_student_subject_idor_protection(app, admin_client):
    """
    Test IDOR protection on subject operations:
    - Attempting to edit or delete subject 2 via student 1's URL returns 403 Forbidden.
    """
    with app.app_context():
        # Seed second student and a subject belonging to student 2
        ins = execute_db(
            """
            INSERT INTO students (name, register_number, department, email, marks)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("Other Student", "REG998", "Data Science", "other@test.edu", 82.0),
            commit=True
        )
        student2_id = ins['lastrowid']

        sub_ins = execute_db(
            """
            INSERT INTO student_subjects (student_id, semester, subject_code, subject_name, credits, marks, grade_point, letter_grade)
            VALUES (?, 1, 'DS101', 'Data Structures', 4, 88.0, 9.0, 'A+')
            """,
            (student2_id,),
            commit=True
        )
        subject2_id = sub_ins['lastrowid']

    # 1. Admin accesses /students/1/subjects/<subject2_id>/edit (IDOR mismatch) -> 403
    res_edit_idor = admin_client.get(f'/students/1/subjects/{subject2_id}/edit')
    assert res_edit_idor.status_code == 403
    assert b"Access Denied" in res_edit_idor.data
    assert b"Subject record does not belong to the specified student" in res_edit_idor.data

    # 2. Admin POSTs to /students/1/subjects/<subject2_id>/edit (IDOR mismatch) -> 403
    res_post_edit_idor = admin_client.post(
        f'/students/1/subjects/{subject2_id}/edit',
        data={'semester': 1, 'subject_code': 'DS101', 'subject_name': 'Tampered', 'credits': 4, 'marks': 99.0}
    )
    assert res_post_edit_idor.status_code == 403

    # 3. Admin POSTs to /students/1/delete-subject/<subject2_id> (IDOR mismatch) -> 403
    res_del_idor = admin_client.post(f'/students/1/delete-subject/{subject2_id}')
    assert res_del_idor.status_code == 403
    assert b"Access Denied" in res_del_idor.data

    # Verify subject2 was NOT deleted or modified
    with app.app_context():
        subj = query_db("SELECT * FROM student_subjects WHERE id = ?", (subject2_id,), one=True)
        assert subj is not None
        assert subj['subject_name'] == 'Data Structures'


def test_unauthenticated_requests_redirect_to_login(client):
    """Unauthenticated access to any protected route must redirect to login."""
    protected_urls = [
        '/dashboard',
        '/students',
        '/students/add',
        '/students/1',
        '/students/1/edit',
        '/students/export',
        '/users',
        '/audit-logs',
        '/students/1/pdf',
        '/change-password',
        '/student-portal',
        '/attendance',
    ]
    for url in protected_urls:
        res = client.get(url, follow_redirects=False)
        assert res.status_code == 302, f"Expected 302 redirect for unauthenticated GET {url}, got {res.status_code}"
        assert '/login' in res.headers.get('Location', '')


def test_route_decorator_registration_ast_audit():
    """AST validation confirming all protected routes in backend/routes.py have @login_required."""
    with open('backend/routes.py', 'r', encoding='utf-8') as f:
        tree = ast.parse(f.read())

    public_endpoints = {'health_check', 'verify_student_marksheet', 'verify_legacy_redirect', 'privacy_policy'}

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            is_route = any(
                isinstance(d, ast.Call) and getattr(d.func, 'attr', '') == 'route'
                for d in node.decorator_list
            )
            if is_route:
                if node.name in public_endpoints:
                    continue
                decorator_names = [
                    ast.unparse(d) for d in node.decorator_list
                    if not (isinstance(d, ast.Call) and getattr(d.func, 'attr', '') == 'route')
                ]
                assert 'login_required' in decorator_names, (
                    f"Route function '{node.name}' in backend/routes.py is missing @login_required decorator!"
                )
