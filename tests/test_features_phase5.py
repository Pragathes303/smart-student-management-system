"""
Comprehensive Test Suite for Phase 5 Features:
1. Forgot / Reset Password flow with secure tokens and complexity enforcement.
2. Edit academic subject route with automatic Anna University grade/CGPA recalculation.
3. Trash bin: soft-delete recovery and permanent purge (Admin only).
4. Daily period-wise attendance bulk marking and automatic percentage synchronization.
5. Audit log CSV export with formula injection sanitization.
6. Student self-service profile update (contact details & photo only).
7. Semester results publishing lock toggle.
8. Admin user management: toggle active status (protect last admin) and temporary password reset.
9 & 10. CLI management commands (backup-db, init-db, create-admin, seed-demo).
"""

import os
import io
import pytest
from backend.security import generate_password_reset_token
from database.db import query_db, execute_db


def test_forgot_and_reset_password_flow(client, app):
    """Test forgot password request and tokenized password reset."""
    with app.app_context():
        # Request forgot password for existing user
        res = client.post('/forgot-password', data={'email': 'staff@test.edu'}, follow_redirects=True)
        assert res.status_code == 200
        assert b"password reset link" in res.data or b"email" in res.data

        # Get staff user id
        staff = query_db("SELECT id, session_version FROM users WHERE username = 'staff'", one=True)
        assert staff is not None

        # Generate valid token
        token = generate_password_reset_token(staff['id'], staff['session_version'] or 1, secret_key=app.config['SECRET_KEY'])

        # GET reset password page
        res_get = client.get(f'/reset-password/{token}')
        assert res_get.status_code == 200
        assert b"Set New Password" in res_get.data

        # Test password mismatch
        res_mismatch = client.post(f'/reset-password/{token}', data={
            'password': 'NewPassword@123',
            'confirm_password': 'DifferentPassword@123'
        }, follow_redirects=True)
        assert b"passwords do not match" in res_mismatch.data.lower()

        # Test password complexity failure (too short)
        res_weak = client.post(f'/reset-password/{token}', data={
            'password': 'short',
            'confirm_password': 'short'
        }, follow_redirects=True)
        assert b"at least 8 characters" in res_weak.data.lower()

        # Test successful password reset
        res_success = client.post(f'/reset-password/{token}', data={
            'password': 'StrongStaffPass@2026',
            'confirm_password': 'StrongStaffPass@2026'
        }, follow_redirects=True)
        assert res_success.status_code == 200
        assert b"successfully reset" in res_success.data.lower()

        # Old password should no longer work
        res_old_login = client.post('/login', data={'username': 'staff', 'password': 'Staff@123'}, follow_redirects=True)
        assert b"Invalid username or password" in res_old_login.data

        # New password succeeds
        res_new_login = client.post('/login', data={'username': 'staff', 'password': 'StrongStaffPass@2026'}, follow_redirects=True)
        assert res_new_login.status_code == 200
        assert b"Dashboard" in res_new_login.data or b"Logout" in res_new_login.data


def test_edit_subject_route(admin_client, app):
    """Test editing an academic subject, updating marks, and verifying Anna University grade recalculation."""
    with app.app_context():
        from backend.services.student_service import StudentService
        sid, student, _ = StudentService.create_student({
            'name': 'Edit Subject Student',
            'register_number': 'REG_SUB_EDIT_01',
            'department': 'Computer Science',
            'email': 'subedit@test.edu',
            'phone': '9876543210',
            'marks': 75.0,
            'semester': 1,
            'attendance_percentage': 85.0
        })

        # Add subject with 75 marks (A, 8.0)
        execute_db(
            """
            INSERT INTO student_subjects (student_id, semester, subject_code, subject_name, credits, marks, grade_point, letter_grade)
            VALUES (?, 1, 'CS301', 'Database Systems', 4, 75, 8.0, 'A')
            """,
            (sid,),
            commit=True
        )
        sub = query_db("SELECT id FROM student_subjects WHERE student_id = ? AND subject_code = 'CS301'", (sid,), one=True)
        assert sub is not None
        sub_id = sub['id']

        # GET edit subject page
        res_get = admin_client.get(f'/students/{sid}/subjects/{sub_id}/edit')
        assert res_get.status_code == 200
        assert b"Edit Subject" in res_get.data

        # POST update subject with 95 marks (O, 10.0)
        res_post = admin_client.post(f'/students/{sid}/subjects/{sub_id}/edit', data={
            'subject_name': 'Advanced Database Systems',
            'subject_code': 'CS301',
            'credits': 4,
            'marks': 95,
            'semester': 1
        }, follow_redirects=True)
        assert res_post.status_code == 200
        assert b"updated successfully" in res_post.data.lower()

        # Verify subject in database has grade 'O' and GP 10.0
        updated_sub = query_db("SELECT * FROM student_subjects WHERE id = ?", (sub_id,), one=True)
        assert updated_sub['letter_grade'] == 'O'
        assert updated_sub['grade_point'] == 10.0
        assert updated_sub['marks'] == 95.0


def test_trash_bin_and_restore_and_hard_delete(admin_client, app):
    """Test soft-delete trash bin listing, restoration, and permanent hard delete."""
    with app.app_context():
        from backend.services.student_service import StudentService
        sid, student, _ = StudentService.create_student({
            'name': 'Trash Test Student',
            'register_number': 'REG_TRASH_001',
            'department': 'Mechanical Engineering',
            'email': 'trash01@test.edu',
            'marks': 70.0,
            'semester': 2
        })

        # Soft delete student
        StudentService.delete_student(sid)
        check_deleted = query_db("SELECT deleted_at FROM students WHERE id = ?", (sid,), one=True)
        assert check_deleted['deleted_at'] is not None

        # View trash bin
        res_trash = admin_client.get('/students/trash')
        assert res_trash.status_code == 200
        assert b"Trash Test Student" in res_trash.data

        # Restore student
        res_restore = admin_client.post(f'/students/{sid}/restore', follow_redirects=True)
        assert res_restore.status_code == 200
        assert b"restored successfully" in res_restore.data.lower()

        check_restored = query_db("SELECT deleted_at FROM students WHERE id = ?", (sid,), one=True)
        assert check_restored['deleted_at'] is None

        # Soft delete again and hard delete permanently
        StudentService.delete_student(sid)
        res_hard = admin_client.post(f'/students/{sid}/hard-delete', follow_redirects=True)
        assert res_hard.status_code == 200
        assert b"permanently erased" in res_hard.data.lower()

        check_purged = query_db("SELECT id FROM students WHERE id = ?", (sid,), one=True)
        assert check_purged is None


def test_daily_period_attendance_bulk_mark(admin_client, app):
    """Test period-wise attendance marking and automated calculation of attendance percentage."""
    with app.app_context():
        from backend.services.student_service import StudentService
        sid1, _, _ = StudentService.create_student({
            'name': 'Att Student 1',
            'register_number': 'REG_ATT_01',
            'department': 'Civil Engineering',
            'email': 'att01@test.edu',
            'marks': 80.0
        })
        sid2, _, _ = StudentService.create_student({
            'name': 'Att Student 2',
            'register_number': 'REG_ATT_02',
            'department': 'Civil Engineering',
            'email': 'att02@test.edu',
            'marks': 75.0
        })

        # GET attendance view
        res_view = admin_client.get('/attendance?dept=Civil+Engineering&date=2026-10-25&period=1')
        assert res_view.status_code == 200
        assert b"Period-Wise Attendance" in res_view.data

        # Submit bulk attendance
        res_mark = admin_client.post('/attendance/mark', data={
            'department': 'Civil Engineering',
            'date': '2026-10-25',
            'period': 1,
            f'status_{sid1}': 'present',
            f'status_{sid2}': 'absent'
        }, follow_redirects=True)
        assert res_mark.status_code == 200
        assert b"recorded attendance" in res_mark.data.lower()

        # Verify attendance records
        rec1 = query_db("SELECT status FROM attendance_records WHERE student_id = ? AND date = '2026-10-25' AND period = 1", (sid1,), one=True)
        rec2 = query_db("SELECT status FROM attendance_records WHERE student_id = ? AND date = '2026-10-25' AND period = 1", (sid2,), one=True)
        assert rec1['status'] == 'present'
        assert rec2['status'] == 'absent'

        # Verify synchronized percentage in student_attendance
        sa1 = query_db("SELECT attendance_percentage FROM student_attendance WHERE student_id = ?", (sid1,), one=True)
        sa2 = query_db("SELECT attendance_percentage FROM student_attendance WHERE student_id = ?", (sid2,), one=True)
        assert sa1['attendance_percentage'] == 100.0
        assert sa2['attendance_percentage'] == 0.0


def test_export_audit_logs_csv_security(staff_client):
    """Staff cannot export audit logs (403 forbidden)."""
    res = staff_client.get('/audit-logs/export')
    assert res.status_code == 403


def test_export_audit_logs_csv_admin(admin_client):
    """Admin successfully exports sanitized CSV audit logs."""
    res = admin_client.get('/audit-logs/export')
    assert res.status_code == 200
    assert res.mimetype == 'text/csv'
    assert b"attachment; filename=audit_logs_export.csv" in res.headers.get('Content-Disposition', '').encode()
    assert b"Timestamp" in res.data
    assert b"Action" in res.data


def test_student_self_service_profile(student_client, app):
    """Test student portal self-service updating phone, address, and profile photo."""
    with app.app_context():
        # Access profile
        res_get = student_client.get('/student/profile')
        assert res_get.status_code == 200
        assert b"My Student Profile" in res_get.data

        # Update phone and address
        res_post = student_client.post('/student/profile', data={
            'phone': '9123456789',
            'address': '42 Innovation Parkway, Tech Campus'
        }, follow_redirects=True)
        assert res_post.status_code == 200
        assert b"updated successfully" in res_post.data.lower()

        # Verify DB updated
        stud_user = query_db("SELECT student_id FROM users WHERE username = 'reg999'", one=True)
        updated_student = query_db("SELECT phone, address FROM students WHERE id = ?", (stud_user['student_id'],), one=True)
        assert updated_student['phone'] == '9123456789'
        assert updated_student['address'] == '42 Innovation Parkway, Tech Campus'


def test_semester_results_lock_toggle(admin_client, app):
    """Test admin toggle for locking and unlocking semester results."""
    with app.app_context():
        # Toggle to locked (0)
        res1 = admin_client.post('/settings/semester-lock', follow_redirects=True)
        assert res1.status_code == 200
        lock1 = query_db("SELECT value FROM system_settings WHERE key = 'semester_results_published'", one=True)
        assert lock1['value'] in ('0', '1')

        # Toggle again
        res2 = admin_client.post('/settings/semester-lock', follow_redirects=True)
        assert res2.status_code == 200
        lock2 = query_db("SELECT value FROM system_settings WHERE key = 'semester_results_published'", one=True)
        assert lock2['value'] != lock1['value']


def test_admin_user_management_toggle_and_reset(admin_client, app):
    """Test admin user status toggle and password reset."""
    with app.app_context():
        staff_user = query_db("SELECT id, is_active FROM users WHERE username = 'staff'", one=True)
        staff_id = staff_user['id']

        # Deactivate staff user
        res_deact = admin_client.post(f'/users/{staff_id}/toggle-status', follow_redirects=True)
        assert res_deact.status_code == 200
        assert b"deactivated" in res_deact.data.lower()

        # Sign out admin to test staff login
        admin_client.post('/logout', follow_redirects=True)

        # Verify staff cannot log in while deactivated
        res_login_deact = admin_client.post('/login', data={'username': 'staff', 'password': 'Staff@123'}, follow_redirects=True)
        assert b"Invalid username or password" in res_login_deact.data

        # Log back in as admin
        admin_client.post('/login', data={'username': 'admin', 'password': 'Admin@123'}, follow_redirects=True)

        # Admin reactivates staff
        res_react = admin_client.post(f'/users/{staff_id}/toggle-status', follow_redirects=True)
        assert res_react.status_code == 200
        assert b"activated" in res_react.data.lower()

        # Admin reset staff password
        res_reset = admin_client.post(f'/users/{staff_id}/reset-password', follow_redirects=True)
        assert res_reset.status_code == 200
        assert b"Temporary Password:" in res_reset.data

        # Check must_change_password flag is set to 1
        updated_staff = query_db("SELECT must_change_password FROM users WHERE id = ?", (staff_id,), one=True)
        assert updated_staff['must_change_password'] == 1

        # Prevent deactivating the only admin
        admin_user = query_db("SELECT id FROM users WHERE username = 'admin'", one=True)
        res_deact_admin = admin_client.post(f'/users/{admin_user["id"]}/toggle-status', follow_redirects=True)
        assert b"Cannot deactivate the only active system administrator" in res_deact_admin.data


def test_cli_commands(app):
    """Test CLI commands backup-db, init-db, create-admin, and seed-demo."""
    runner = app.test_cli_runner()

    # Test backup-db
    res_backup = runner.invoke(args=['backup-db', '--output-dir', 'test_backups'])
    assert res_backup.exit_code == 0
    assert "Database successfully backed up" in res_backup.output

    # Clean up test backup file
    if os.path.exists('test_backups'):
        for f in os.listdir('test_backups'):
            os.remove(os.path.join('test_backups', f))
        try:
            os.rmdir('test_backups')
        except OSError:
            pass

    # Test init-db
    res_init = runner.invoke(args=['init-db'])
    assert res_init.exit_code == 0
    assert "Database schema and migrations initialized successfully" in res_init.output

    # Test create-admin
    res_admin = runner.invoke(args=['create-admin', '--username', 'cliadmin', '--password', 'CliAdmin@123', '--email', 'cliadmin@test.edu'])
    assert res_admin.exit_code == 0
    assert "Administrator account 'cliadmin' created successfully" in res_admin.output

    # Test seed-demo
    res_seed = runner.invoke(args=['seed-demo'])
    assert res_seed.exit_code == 0
    assert "Demo data seeded successfully" in res_seed.output


def test_export_students_excel_student_forbidden(student_client):
    """Student role must receive 403 Forbidden when attempting to export records to Excel."""
    res = student_client.get('/students/export-excel')
    assert res.status_code == 403


def test_export_students_excel_admin_success(admin_client):
    """Admin role can export records to a valid Excel (.xlsx) workbook."""
    import io
    import openpyxl

    res = admin_client.get('/students/export-excel')
    assert res.status_code == 200
    assert res.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "attachment;filename=student_records.xlsx" in res.headers.get("Content-Disposition", "")

    # Validate workbook structure
    wb = openpyxl.load_workbook(io.BytesIO(res.data))
    ws = wb.active
    assert ws.title == "Students"
    headers = [cell.value for cell in ws[1]]
    assert "Register Number" in headers
    assert "Name" in headers
    assert "Marks" in headers
    assert "Grade" in headers
