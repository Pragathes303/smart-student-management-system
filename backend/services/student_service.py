"""
Student Domain Service
Encapsulates all core student mutations in atomic transactions:
creation, linked portal user provisioning, attendance initialization,
CGPA and derived marks synchronization, and soft/hard deletion.
"""

from typing import Any

from werkzeug.security import generate_password_hash

from backend.cache import dashboard_cache
from backend.security import generate_secure_token, generate_temp_password
from backend.utils import calculate_gpa
from database.db import execute_db, query_db, transaction


class StudentService:
    """Service handling all student lifecycle operations."""

    @staticmethod
    def create_student(
        cleaned: dict[str, Any],
        photo_filename: str | None = None,
        created_by_user_id: int | None = None,
        default_total_classes: int = 60,
        default_attended_classes: int = 52
    ) -> tuple[int, dict[str, Any], str]:
        """
        Atomically create a student, attendance record, and student portal account (B1, B2).
        
        Returns:
            Tuple[int, dict, str]: (student_id, student_record, generated_temp_password)
        """
        verify_token = generate_secure_token(24)
        temp_pwd = generate_temp_password(14)
        pwd_hash = generate_password_hash(temp_pwd, method="pbkdf2:sha256")
        portal_username = cleaned['register_number'].strip().lower()

        phone_val = cleaned.get('phone')
        dob_val = cleaned.get('dob')
        year_val = int(cleaned.get('year', 1))

        with transaction() as db:
            # 1. Insert Student Record
            ins = execute_db(
                """
                INSERT INTO students (name, register_number, department, year, email, marks, photo_filename, phone, dob, created_by, verify_token)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cleaned['name'],
                    cleaned['register_number'],
                    cleaned['department'],
                    year_val,
                    cleaned['email'],
                    cleaned['marks'],
                    photo_filename,
                    phone_val,
                    dob_val,
                    created_by_user_id,
                    verify_token
                ),
                commit=False,
                custom_db=db
            )
            student_id = ins['lastrowid']

            # 2. Insert Student Attendance Record
            pct = round((default_attended_classes / default_total_classes) * 100.0, 2) if default_total_classes > 0 else 0.0
            execute_db(
                """
                INSERT INTO student_attendance (student_id, total_classes, attended_classes, attendance_percentage)
                VALUES (?, ?, ?, ?)
                """,
                (student_id, default_total_classes, default_attended_classes, pct),
                commit=False,
                custom_db=db
            )

            # 3. Create or Link Student Portal User Account
            existing_user = query_db(
                "SELECT id FROM users WHERE username = ? OR email = ?",
                (portal_username, cleaned['email']),
                one=True,
                custom_db=db
            )
            if not existing_user:
                execute_db(
                    """
                    INSERT INTO users (username, password_hash, role, full_name, email, student_id, is_active, must_change_password, session_version)
                    VALUES (?, ?, 'student', ?, ?, ?, 1, 1, 1)
                    """,
                    (portal_username, pwd_hash, cleaned['name'], cleaned['email'], student_id),
                    commit=False,
                    custom_db=db
                )
            else:
                # Link existing user record to this student
                execute_db(
                    "UPDATE users SET student_id = ? WHERE id = ?",
                    (student_id, existing_user['id']),
                    commit=False,
                    custom_db=db
                )

        # Record initial enrollment marks history
        from backend.logger import record_marks_history
        from backend.utils import calculate_grade
        init_grade = calculate_grade(cleaned['marks'])['grade']
        record_marks_history(
            student_id=student_id,
            old_marks=0.0,
            new_marks=cleaned['marks'],
            changed_by=created_by_user_id,
            changed_by_name="system",
            old_grade=None,
            new_grade=init_grade,
            subject_code="OVERALL",
            subject_name="Initial Marks",
            reason="Initial student enrollment"
        )

        student_record = query_db("SELECT * FROM students WHERE id = ?", (student_id,), one=True)
        dashboard_cache.invalidate()
        return student_id, dict(student_record), temp_pwd

    @staticmethod
    def update_student(
        student_id: int,
        cleaned: dict[str, Any],
        photo_filename: str | None = None,
        changed_by_id: int | None = None,
        changed_by_name: str = "system",
        reason: str | None = None,
        ip_address: str | None = None
    ) -> dict[str, Any]:
        """
        Update an existing student record, audit mark modifications, and synchronize linked portal credentials (B6).
        """
        phone_val = cleaned.get('phone')
        dob_val = cleaned.get('dob')
        year_val = int(cleaned.get('year', 1))

        old_student = query_db("SELECT * FROM students WHERE id = ?", (student_id,), one=True)

        with transaction() as db:
            if photo_filename:
                execute_db(
                    """
                    UPDATE students
                    SET name = ?, register_number = ?, department = ?, year = ?, email = ?,
                        marks = ?, photo_filename = ?,
                        phone = COALESCE(?, phone),
                        dob = COALESCE(?, dob),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        cleaned['name'],
                        cleaned['register_number'],
                        cleaned['department'],
                        year_val,
                        cleaned['email'],
                        cleaned['marks'],
                        photo_filename,
                        phone_val,
                        dob_val,
                        student_id
                    ),
                    commit=False,
                    custom_db=db
                )
            else:
                execute_db(
                    """
                    UPDATE students
                    SET name = ?, register_number = ?, department = ?, year = ?, email = ?,
                        marks = ?,
                        phone = COALESCE(?, phone),
                        dob = COALESCE(?, dob),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        cleaned['name'],
                        cleaned['register_number'],
                        cleaned['department'],
                        year_val,
                        cleaned['email'],
                        cleaned['marks'],
                        phone_val,
                        dob_val,
                        student_id
                    ),
                    commit=False,
                    custom_db=db
                )

            # B6: Synchronize linked student portal username and email
            new_username = cleaned['register_number'].strip().lower()
            execute_db(
                """
                UPDATE users
                SET username = ?, email = ?, full_name = ?
                WHERE student_id = ?
                """,
                (new_username, cleaned['email'], cleaned['name'], student_id),
                commit=False,
                custom_db=db
            )

        # Track marks edit history if marks value modified
        if old_student and abs(float(old_student['marks']) - float(cleaned['marks'])) > 1e-6:
            from backend.logger import record_marks_history
            from backend.utils import calculate_grade
            old_gr = calculate_grade(old_student['marks'])['grade']
            new_gr = calculate_grade(cleaned['marks'])['grade']
            record_marks_history(
                student_id=student_id,
                old_marks=old_student['marks'],
                new_marks=cleaned['marks'],
                changed_by=changed_by_id,
                changed_by_name=changed_by_name,
                old_grade=old_gr,
                new_grade=new_gr,
                subject_code="OVERALL",
                subject_name="Overall Marks",
                reason=reason or "Student record edit",
                ip_address=ip_address
            )

        # Recalculate CGPA and derived marks if subjects exist (B4)
        StudentService.recalculate_cgpa(student_id)
        dashboard_cache.invalidate()
        return dict(query_db("SELECT * FROM students WHERE id = ?", (student_id,), one=True))

    @staticmethod
    def recalculate_cgpa(student_id: int) -> float:
        """
        Recalculate GPA across all student subjects and synchronize CGPA and derived marks (B3, B4).
        """
        subjects = query_db(
            "SELECT * FROM student_subjects WHERE student_id = ? ORDER BY semester, subject_code",
            (student_id,)
        )
        if not subjects:
            return 0.0

        subj_dicts = [dict(s) for s in subjects]
        gpa_stats = calculate_gpa(subj_dicts)
        new_gpa = gpa_stats['gpa']

        # B4: When subjects exist, derive overall marks from subject average
        derived_marks = round(sum(float(s['marks']) for s in subj_dicts) / len(subj_dicts), 2)

        execute_db(
            "UPDATE students SET cgpa = ?, marks = ? WHERE id = ?",
            (new_gpa, derived_marks, student_id),
            commit=True
        )
        return new_gpa

    @staticmethod
    def soft_delete_student(student_id: int) -> bool:
        """Soft delete student record and deactivate linked portal account."""
        with transaction() as db:
            execute_db(
                "UPDATE students SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?",
                (student_id,),
                commit=False,
                custom_db=db
            )
            execute_db(
                "UPDATE users SET is_active = 0 WHERE student_id = ?",
                (student_id,),
                commit=False,
                custom_db=db
            )
        dashboard_cache.invalidate()
        return True

    delete_student = soft_delete_student

    @staticmethod
    def restore_student(student_id: int) -> bool:
        """Restore a soft-deleted student record."""
        with transaction() as db:
            execute_db(
                "UPDATE students SET deleted_at = NULL WHERE id = ?",
                (student_id,),
                commit=False,
                custom_db=db
            )
            execute_db(
                "UPDATE users SET is_active = 1 WHERE student_id = ?",
                (student_id,),
                commit=False,
                custom_db=db
            )
        dashboard_cache.invalidate()
        return True

    @staticmethod
    def hard_delete_student(student_id: int) -> bool:
        """Permanently delete student record and associated storage files."""
        from backend.storage import get_storage_handler
        student = query_db("SELECT photo_filename FROM students WHERE id = ?", (student_id,), one=True)
        if student and student['photo_filename']:
            try:
                storage = get_storage_handler()
                storage.delete(student['photo_filename'])
            except Exception:
                pass
        execute_db("DELETE FROM students WHERE id = ?", (student_id,), commit=True)
        dashboard_cache.invalidate()
        return True

    @staticmethod
    def anonymize_student(student_id: int) -> bool:
        """
        Permanently anonymize a student record under data privacy principles (Right to Erasure / Minimization).
        Scrubs all Personally Identifiable Information (PII):
        - Name is replaced with 'Anonymized Student #{id}'
        - Register number is replaced with 'ANON{sha256[:8].upper()}'
        - Email is replaced with 'anonymized.{id}@privacy.local'
        - Phone, DOB, address, and verify_token are cleared (set to NULL)
        - Photo file is permanently deleted from storage and photo_filename is set to NULL
        - Associated user portal login is deactivated and credentials/PII scrambled
        - Academic metrics (department, marks, grades, subjects) are preserved for institutional stats
        """
        import hashlib
        from datetime import datetime

        from backend.storage import get_storage_handler

        student = query_db("SELECT id, name, register_number, photo_filename FROM students WHERE id = ?", (student_id,), one=True)
        if not student:
            return False

        # 1. Delete physical photo from storage
        if student['photo_filename']:
            try:
                storage = get_storage_handler()
                storage.delete(student['photo_filename'])
            except Exception:
                pass

        # 2. Generate non-reversible pseudonyms
        anon_name = f"Anonymized Student #{student_id}"
        anon_hash = hashlib.sha256(f"anon-student-{student_id}".encode()).hexdigest()[:8].upper()
        anon_reg = f"ANON{anon_hash}"
        anon_email = f"anonymized.{student_id}@privacy.local"
        now_iso = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        with transaction() as db:
            # 3. Anonymize student row
            execute_db(
                """
                UPDATE students
                SET name = ?,
                    register_number = ?,
                    email = ?,
                    phone = NULL,
                    dob = NULL,
                    address = NULL,
                    photo_filename = NULL,
                    verify_token = NULL,
                    deleted_at = COALESCE(deleted_at, ?),
                    updated_at = ?
                WHERE id = ?
                """,
                (anon_name, anon_reg, anon_email, now_iso, now_iso, student_id),
                commit=False,
                custom_db=db
            )

            # 4. Deactivate and scramble any linked student user login
            execute_db(
                """
                UPDATE users
                SET username = ?,
                    full_name = ?,
                    email = ?,
                    password_hash = 'ANONYMIZED_CREDENTIAL',
                    is_active = 0,
                    session_version = session_version + 1
                WHERE student_id = ?
                """,
                (f"anon_user_{student_id}", anon_name, f"anon_user.{student_id}@privacy.local", student_id),
                commit=False,
                custom_db=db
            )

        dashboard_cache.invalidate()
        return True
