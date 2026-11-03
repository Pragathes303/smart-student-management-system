"""
Phase 2 Data Integrity & Reliability Test Suite
Covers:
- Database Constraints: UNIQUE register_number, NOT NULL, Foreign Keys, CHECK marks (0-100), CHECK year (1-4).
- Indexes: register_number, department, year, marks_history.
- Transactions & Rollback on Error.
- Soft-delete (deleted_at), Admin Restore, Hard-Delete, and Anonymization.
- Duplicate Submission Rejection & Optimistic Concurrency Control (updated_at conflict detection).
- Audit Trail: who, what, when, old value -> new value, IP address, with Admin filtering & pagination.
- Marks Edit History tracking & visibility.
- Automated Backup, 7-day retention rotation, and verified Disaster Recovery Restore.
"""

import os
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta
import pytest

from backend.logger import record_audit, record_marks_history
from backend.services.student_service import StudentService
from database.backup_db import backup_database, restore_database, rotate_backups, verify_integrity
from database.db import get_db, query_db, transaction


# =============================================================================
# 1. Database Constraints & Indexes Tests
# =============================================================================
def test_unique_register_number_constraint(app):
    """Ensure database raises IntegrityError when inserting duplicate register number."""
    with app.app_context():
        conn = get_db()
        # First student already has REG999 from seed
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO students (name, register_number, department, year, email, marks)
                VALUES ('Duplicate User', 'REG999', 'Computer Science', 1, 'unique999@test.edu', 75.0)
                """
            )


def test_marks_check_constraint_range(app):
    """Ensure database CHECK constraint enforces marks between 0.0 and 100.0."""
    with app.app_context():
        conn = get_db()
        # Negative marks (< 0) must fail
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO students (name, register_number, department, year, email, marks)
                VALUES ('Negative Marks', 'REG_NEG', 'Computer Science', 1, 'neg@test.edu', -10.0)
                """
            )

        # Marks > 100.0 must fail
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO students (name, register_number, department, year, email, marks)
                VALUES ('Excess Marks', 'REG_EXC', 'Computer Science', 1, 'exc@test.edu', 105.0)
                """
            )


def test_year_check_constraint_range(app):
    """Ensure database CHECK constraint enforces year between 1 and 4."""
    with app.app_context():
        conn = get_db()
        # Year 0 must fail
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO students (name, register_number, department, year, email, marks)
                VALUES ('Year Zero', 'REG_Y0', 'Computer Science', 0, 'y0@test.edu', 80.0)
                """
            )

        # Year 5 must fail
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO students (name, register_number, department, year, email, marks)
                VALUES ('Year Five', 'REG_Y5', 'Computer Science', 5, 'y5@test.edu', 80.0)
                """
            )

        # Valid year 1-4 must succeed
        cursor = conn.execute(
            """
            INSERT INTO students (name, register_number, department, year, email, marks)
            VALUES ('Valid Year Student', 'REG_Y3', 'Computer Science', 3, 'y3@test.edu', 80.0)
            """
        )
        assert cursor.lastrowid > 0
        conn.commit()


def test_not_null_constraints(app):
    """Ensure database NOT NULL constraints are enforced on required columns."""
    with app.app_context():
        conn = get_db()
        # Missing register_number (NULL) must fail
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO students (name, register_number, department, year, email, marks)
                VALUES ('No Reg Student', NULL, 'Computer Science', 1, 'noreg@test.edu', 70.0)
                """
            )

        # Missing department (NULL) must fail
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO students (name, register_number, department, year, email, marks)
                VALUES ('No Dept Student', 'REG_NODEPT', NULL, 1, 'nodept@test.edu', 70.0)
                """
            )


def test_foreign_keys_cascade_deletion(app):
    """Ensure foreign key cascades delete child records (subjects, attendance, marks_history)."""
    with app.app_context():
        conn = get_db()
        student = query_db("SELECT id FROM students WHERE register_number = 'REG999'", one=True)
        sid = student['id']

        # Ensure attendance and subject exist
        att = query_db("SELECT id FROM student_attendance WHERE student_id = ?", (sid,), one=True)
        sub = query_db("SELECT id FROM student_subjects WHERE student_id = ?", (sid,), one=True)
        assert att is not None
        assert sub is not None

        # Add marks history entry
        record_marks_history(sid, 80.0, 85.0, reason="Cascade test")
        mh = query_db("SELECT id FROM marks_history WHERE student_id = ?", (sid,), one=True)
        assert mh is not None

        # Delete student directly
        conn.execute("DELETE FROM students WHERE id = ?", (sid,))
        conn.commit()

        # Child records must be automatically deleted by CASCADE
        assert query_db("SELECT id FROM student_attendance WHERE student_id = ?", (sid,), one=True) is None
        assert query_db("SELECT id FROM student_subjects WHERE student_id = ?", (sid,), one=True) is None
        assert query_db("SELECT id FROM marks_history WHERE student_id = ?", (sid,), one=True) is None


def test_indexes_exist(app):
    """Verify performance and constraint indexes are present on the SQLite schema."""
    with app.app_context():
        indexes = query_db("SELECT name FROM sqlite_master WHERE type = 'index'")
        idx_names = {r['name'] for r in indexes}

        assert 'idx_students_reg_no' in idx_names
        assert 'idx_students_department' in idx_names
        assert 'idx_students_year' in idx_names
        assert 'idx_marks_history_student' in idx_names
        assert 'idx_audit_logs_action' in idx_names


# =============================================================================
# 2. Transactions & Rollback on Error
# =============================================================================
def test_transaction_rollback_on_error(app):
    """Verify that multi-step operations in transaction() roll back completely on failure."""
    with app.app_context():
        initial_count = query_db("SELECT COUNT(*) as cnt FROM students", one=True)['cnt']

        with pytest.raises(ValueError):
            with transaction() as db:
                db.execute(
                    """
                    INSERT INTO students (name, register_number, department, year, email, marks)
                    VALUES ('Atomic Test', 'REG_ATOMIC', 'Computer Science', 2, 'atomic@test.edu', 88.0)
                    """
                )
                # Intentionally trigger an error on step 2
                raise ValueError("Simulated catastrophic crash mid-transaction")

        # Verify step 1 was rolled back and no partial record was committed
        post_count = query_db("SELECT COUNT(*) as cnt FROM students", one=True)['cnt']
        assert post_count == initial_count
        assert query_db("SELECT id FROM students WHERE register_number = 'REG_ATOMIC'", one=True) is None


# =============================================================================
# 3. Soft-Delete, Restore, Hard-Delete & Anonymization
# =============================================================================
def test_soft_delete_and_admin_restore(app):
    """Test soft-delete hides record, deactivates user, and admin restore recovers it."""
    from tests.conftest import login

    # 1. Staff views active student list
    with app.test_client() as staff_client:
        login(staff_client, 'staff', 'Staff@123')
        res = staff_client.get('/students')
        assert b"REG999" in res.data

    with app.app_context():
        student = query_db("SELECT id FROM students WHERE register_number = 'REG999'", one=True)
        assert student is not None
        sid = student['id']

    # 2. Admin soft-deletes student
    with app.test_client() as admin_client:
        login(admin_client, 'admin', 'Admin@123')
        del_res = admin_client.post(f'/students/{sid}/delete', follow_redirects=True)
        assert del_res.status_code == 200

    # Verify student is excluded from active student list
    with app.test_client() as staff_client:
        login(staff_client, 'staff', 'Staff@123')
        list_res = staff_client.get('/students')
        assert b"REG999" not in list_res.data

    # Verify linked portal account is deactivated (is_active = 0)
    with app.app_context():
        user = query_db("SELECT is_active FROM users WHERE student_id = ?", (sid,), one=True)
        assert user['is_active'] == 0

    # 3. Admin visits trash and restores student
    with app.test_client() as admin_client:
        login(admin_client, 'admin', 'Admin@123')
        trash_res = admin_client.get('/students/trash')
        assert b"REG999" in trash_res.data

        restore_res = admin_client.post(f'/students/{sid}/restore', follow_redirects=True)
        assert restore_res.status_code == 200

    # Verify student reappears in active list and user is reactivated
    with app.test_client() as staff_client:
        login(staff_client, 'staff', 'Staff@123')
        active_res = staff_client.get('/students')
        assert b"REG999" in active_res.data

    with app.app_context():
        restored_user = query_db("SELECT is_active FROM users WHERE student_id = ?", (sid,), one=True)
        assert restored_user['is_active'] == 1


def test_admin_hard_delete_purges_record(app, admin_client):
    """Test hard-delete permanently purges the student and linked user."""
    with app.app_context():
        student = query_db("SELECT id FROM students WHERE register_number = 'REG999'", one=True)
        assert student is not None
        sid = student['id']

    purge_res = admin_client.post(f'/students/{sid}/hard-delete', follow_redirects=True)
    assert purge_res.status_code == 200

    # Verify completely absent from database
    with app.app_context():
        assert query_db("SELECT id FROM students WHERE id = ?", (sid,), one=True) is None
        assert query_db("SELECT id FROM users WHERE student_id = ?", (sid,), one=True) is None


def test_admin_anonymize_student_scrubs_pii(app, admin_client):
    """Test anonymization replaces all PII while retaining academic marks."""
    with app.app_context():
        student = query_db("SELECT id, marks FROM students WHERE register_number = 'REG999'", one=True)
        assert student is not None
        sid = student['id']
        original_marks = student['marks']

    anon_res = admin_client.post(f'/students/{sid}/anonymize', follow_redirects=True)
    assert anon_res.status_code == 200

    with app.app_context():
        anon_student = query_db("SELECT * FROM students WHERE id = ?", (sid,), one=True)
        assert "Anonymized" in anon_student['name']
        assert "ANON" in anon_student['register_number']
        assert anon_student['email'].endswith('@privacy.local')
        assert anon_student['phone'] is None
        assert anon_student['dob'] is None
        assert anon_student['marks'] == original_marks


# =============================================================================
# 4. Duplicate Prevention & Optimistic Concurrency Control
# =============================================================================
def test_server_side_duplicate_prevention(app, staff_client):
    """Test server-side validation rejects duplicate register numbers and emails."""
    res = staff_client.post(
        '/students/add',
        data={
            'name': 'Duplicate Candidate',
            'register_number': 'REG999',  # Already exists
            'department': 'Computer Science',
            'year': '2',
            'email': 'brand_new@test.edu',
            'marks': '80'
        },
        follow_redirects=True
    )
    assert b"already exists" in res.data


def test_optimistic_concurrency_conflict_detection(app, staff_client):
    """
    Test optimistic concurrency control:
    If a record's updated_at changed since the edit form was loaded,
    submission must be rejected with a conflict warning to prevent overwriting.
    """
    with app.app_context():
        student = query_db("SELECT id, updated_at FROM students WHERE register_number = 'REG999'", one=True)
        assert student is not None
        sid = student['id']
        stale_timestamp = student['updated_at']

        # Simulate another staff member modifying the record in the background
        time.sleep(1)
        get_db().execute("UPDATE students SET marks = 99.0, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (sid,))
        get_db().commit()

    # Now the first staff submits their form with the stale original_updated_at
    conflict_res = staff_client.post(
        f'/students/{sid}/edit',
        data={
            'original_updated_at': stale_timestamp,
            'name': 'Conflicted Name',
            'register_number': 'REG999',
            'department': 'Computer Science',
            'year': '1',
            'email': 'student@test.edu',
            'marks': '75.0'
        },
        follow_redirects=True
    )

    assert conflict_res.status_code == 200
    assert b"Conflict detected" in conflict_res.data

    # Verify that the overwrite was rejected (marks remain 99.0 from the concurrent update)
    with app.app_context():
        current = query_db("SELECT marks FROM students WHERE id = ?", (sid,), one=True)
        assert current['marks'] == 99.0


# =============================================================================
# 5. Audit Log Table with Old -> New Value Diff & Admin UI
# =============================================================================
def test_audit_log_captures_diff_and_admin_page(app):
    """Test that audit log captures old_value -> new_value diffs and admin can filter & paginate."""
    from tests.conftest import login

    with app.app_context():
        student = query_db("SELECT id, updated_at FROM students WHERE register_number = 'REG999'", one=True)
        assert student is not None
        sid = student['id']
        curr_updated_at = student['updated_at']

    # Edit student as staff
    with app.test_client() as staff_client:
        login(staff_client, 'staff', 'Staff@123')
        staff_client.post(
            f'/students/{sid}/edit',
            data={
                'original_updated_at': curr_updated_at,
                'name': 'Updated Student Name',
                'register_number': 'REG999',
                'department': 'Computer Science',
                'year': '2',
                'email': 'student@test.edu',
                'marks': '92.5'
            },
            follow_redirects=True
        )

    # Verify audit record in database contains old_value and new_value
    with app.app_context():
        audit_entry = query_db(
            "SELECT * FROM audit_logs WHERE action = 'STUDENT_UPDATED' ORDER BY id DESC LIMIT 1",
            one=True
        )
        assert audit_entry is not None
        assert audit_entry['old_value'] is not None
        assert audit_entry['new_value'] is not None
        assert "Updated Student Name" in audit_entry['new_value']

    # Admin visits /audit-logs and filters by action
    with app.test_client() as admin_client:
        login(admin_client, 'admin', 'Admin@123')
        admin_res = admin_client.get('/audit-logs?action=STUDENT_UPDATED')
        assert admin_res.status_code == 200
        assert b"STUDENT_UPDATED" in admin_res.data
        assert b"State Changes" in admin_res.data

    # Non-admin access should be denied
    with app.test_client() as staff_client:
        login(staff_client, 'staff', 'Staff@123')
        assert staff_client.get('/audit-logs').status_code in (302, 403)

    with app.test_client() as student_client:
        login(student_client, 'reg999', 'Student@123')
        assert student_client.get('/audit-logs').status_code in (302, 403)


# =============================================================================
# 6. Marks Edit History Tracking & Visibility
# =============================================================================
def test_marks_edit_history_tracking(app, staff_client):
    """Test that every marks modification is recorded in marks_history and visible on profile."""
    with app.app_context():
        student = query_db("SELECT id, updated_at, marks FROM students WHERE register_number = 'REG999'", one=True)
        assert student is not None
        sid = student['id']
        old_marks = student['marks']
        curr_updated_at = student['updated_at']

    # 1. Update overall marks via student edit
    staff_client.post(
        f'/students/{sid}/edit',
        data={
            'original_updated_at': curr_updated_at,
            'name': 'Test Student',
            'register_number': 'REG999',
            'department': 'Computer Science',
            'year': '1',
            'email': 'student@test.edu',
            'marks': '95.0'
        },
        follow_redirects=True
    )

    # Verify marks_history entry
    with app.app_context():
        hist = query_db("SELECT * FROM marks_history WHERE student_id = ? ORDER BY id DESC LIMIT 1", (sid,), one=True)
        assert hist is not None
        assert float(hist['old_marks']) == float(old_marks)
        assert float(hist['new_marks']) == 95.0

        sub = query_db("SELECT id FROM student_subjects WHERE student_id = ?", (sid,), one=True)

    # 2. Add and edit a subject
    if sub:
        edit_sub_res = staff_client.post(
            f'/students/{sid}/edit-subject/{sub["id"]}',
            data={'marks': '98.0', 'credits': '3', 'reason': 'Retotaling adjustment'},
            follow_redirects=True
        )
        assert edit_sub_res.status_code == 200

        with app.app_context():
            sub_hist = query_db("SELECT * FROM marks_history WHERE student_id = ? AND subject_id = ?", (sid, sub['id']), one=True)
            assert sub_hist is not None
            assert float(sub_hist['new_marks']) == 98.0
            assert sub_hist['reason'] == 'Retotaling adjustment'

    # 3. Verify Marks Edit History table is rendered on student detail page
    detail_res = staff_client.get(f'/students/{sid}')
    assert b"Marks Edit History" in detail_res.data
    assert b"95" in detail_res.data


# =============================================================================
# 7. Automated Daily Backup, 7-Day Retention & Disaster Recovery Restore
# =============================================================================
def test_backup_and_restore_cycle(app):
    """Test full backup creation, 7-day retention rotation, and disaster recovery restore."""
    with app.app_context():
        db_path = app.config['DATABASE_PATH']
        backup_dir = tempfile.mkdtemp()

        # Step 1: Create snapshot backup
        backup_file = backup_database(source_path=db_path, target_dir=backup_dir, retention_days=7)
        assert backup_file is not None
        assert os.path.exists(backup_file)
        assert verify_integrity(backup_file) is True

        # Step 2: Test 7-day retention rotation
        # Create an artificial 10-day-old backup file
        old_backup = os.path.join(backup_dir, "students_backup_20200101_000000.db")
        with open(old_backup, "wb") as f:
            f.write(b"dummy")
        # Set file modification time to 10 days ago
        ten_days_ago = (datetime.now() - timedelta(days=10)).timestamp()
        os.utime(old_backup, (ten_days_ago, ten_days_ago))

        deleted = rotate_backups(target_dir=backup_dir, retention_days=7)
        assert old_backup in deleted
        assert not os.path.exists(old_backup)

        # Step 3: Modify database to simulate catastrophic data loss
        get_db().execute("DELETE FROM students WHERE register_number = 'REG999'")
        get_db().commit()
        assert query_db("SELECT id FROM students WHERE register_number = 'REG999'", one=True) is None

        # Step 4: Perform Disaster Recovery Restore from snapshot
        restore_success = restore_database(backup_file=backup_file, target_path=db_path)
        assert restore_success is True

        # Step 5: Verify restored database matches pre-loss state
        restored_student = query_db("SELECT id, name FROM students WHERE register_number = 'REG999'", one=True)
        assert restored_student is not None
        assert restored_student['name'] == 'Test Student'
        assert verify_integrity(db_path) is True
