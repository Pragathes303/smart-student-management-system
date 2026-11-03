"""
Automated Test Suite for Smart Student Management System
Tests authentication, RBAC, input validation, student CRUD operations,
search & filtering, dashboard analytics, and storage abstractions.
"""

import os
import sys
import unittest
import tempfile
import sqlite3
import io

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.app import create_app
from backend.config import Config
from backend.utils import validate_student_data, calculate_grade, allowed_file
from backend.storage import LocalStorageHandler
from database.db import init_db, query_db, execute_db
from werkzeug.security import generate_password_hash


class TestStudentSystem(unittest.TestCase):
    """Integration and Unit Test Cases."""

    def setUp(self):
        """Set up an isolated test database and test client before each test."""
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix='.db')
        self.temp_upload_dir = tempfile.mkdtemp()

        class TestConfig(Config):
            TESTING = True
            SECRET_KEY = 'test-secret-key-12345'
            DATABASE_PATH = self.temp_db_path
            UPLOAD_FOLDER = self.temp_upload_dir
            STORAGE_BACKEND = 'local'
            WTF_CSRF_ENABLED = False

        self.app = create_app(TestConfig)
        self.client = self.app.test_client()

        # Seed test database
        with self.app.app_context():
            init_db(self.temp_db_path)
            conn = sqlite3.connect(self.temp_db_path)
            conn.row_factory = sqlite3.Row

            # Add test admin
            admin_pwd = generate_password_hash("Admin@123", method="pbkdf2:sha256")
            execute_db(
                "INSERT INTO users (username, password_hash, role, full_name, email) VALUES (?, ?, ?, ?, ?)",
                ("admin", admin_pwd, "admin", "Admin User", "admin@test.edu"),
                custom_db=conn
            )

            # Add test staff
            staff_pwd = generate_password_hash("Staff@123", method="pbkdf2:sha256")
            execute_db(
                "INSERT INTO users (username, password_hash, role, full_name, email) VALUES (?, ?, ?, ?, ?)",
                ("staff", staff_pwd, "staff", "Staff User", "staff@test.edu"),
                custom_db=conn
            )

            # Add initial test student with secure verification token
            execute_db(
                "INSERT INTO students (name, register_number, department, email, marks, verify_token) VALUES (?, ?, ?, ?, ?, ?)",
                ("Test Student", "REG999", "Computer Science", "test@test.edu", 85.0, "verify_token_reg999_sample"),
                custom_db=conn
            )
            conn.close()

    def tearDown(self):
        """Clean up temporary test files."""
        os.close(self.temp_db_fd)
        if os.path.exists(self.temp_db_path):
            try:
                os.remove(self.temp_db_path)
            except OSError:
                pass

    def login(self, username, password):
        """Helper to sign in via HTTP client."""
        return self.client.post('/login', data={
            'username': username,
            'password': password
        }, follow_redirects=True)

    def logout(self):
        """Helper to sign out (POST required in Phase 2)."""
        return self.client.post('/logout', follow_redirects=True)

    # -------------------------------------------------------------------------
    # Authentication Tests
    # -------------------------------------------------------------------------
    def test_login_successful(self):
        """Test successful authentication with valid credentials."""
        res = self.login("admin", "Admin@123")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Academic Dashboard", res.data)
        self.assertIn(b"Admin User", res.data)

    def test_login_invalid_password(self):
        """Test authentication rejection on wrong password."""
        res = self.login("admin", "WrongPassword")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Invalid username or password", res.data)

    def test_unauthorized_redirect(self):
        """Test unauthenticated user is redirected to login page."""
        res = self.client.get('/dashboard', follow_redirects=False)
        self.assertEqual(res.status_code, 302)
        self.assertIn('/login', res.headers['Location'])

    # -------------------------------------------------------------------------
    # Role-Based Access Control (RBAC) Tests
    # -------------------------------------------------------------------------
    def test_staff_cannot_access_user_management(self):
        """Verify that staff members are blocked from admin user management."""
        self.login("staff", "Staff@123")
        res = self.client.get('/users', follow_redirects=False)
        self.assertEqual(res.status_code, 403)

    def test_staff_cannot_delete_student(self):
        """Verify that staff members cannot delete student records."""
        self.login("staff", "Staff@123")
        # Find student ID
        with self.app.app_context():
            student = query_db("SELECT id FROM students WHERE register_number = 'REG999'", one=True)
            student_id = student['id']

        res = self.client.post(f'/students/{student_id}/delete', follow_redirects=False)
        self.assertEqual(res.status_code, 403)

    def test_admin_can_access_user_management(self):
        """Verify that admin can access user management."""
        self.login("admin", "Admin@123")
        res = self.client.get('/users', follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Staff &amp; User Accounts", res.data)

    # -------------------------------------------------------------------------
    # Validation & Utilities Tests
    # -------------------------------------------------------------------------
    def test_grade_calculation(self):
        """Verify Anna University 10-point scale grade tiers and labels (B11)."""
        self.assertEqual(calculate_grade(95)['grade'], 'O')
        self.assertEqual(calculate_grade(85)['grade'], 'A+')
        self.assertEqual(calculate_grade(75)['grade'], 'A')
        self.assertEqual(calculate_grade(65)['grade'], 'B+')
        self.assertEqual(calculate_grade(52)['grade'], 'B')
        self.assertEqual(calculate_grade(45)['grade'], 'RA')
        self.assertFalse(calculate_grade(45)['passed'])

    def test_validate_student_data_valid(self):
        """Verify valid student data passes validation."""
        with self.app.app_context():
            valid, errors, cleaned = validate_student_data(
                "John Doe", "REG101", "Computer Science", "john@university.edu", "92.5"
            )
            self.assertTrue(valid)
            self.assertEqual(len(errors), 0)
            self.assertEqual(cleaned['marks'], 92.5)

    def test_validate_student_data_invalid_marks(self):
        """Verify marks outside 0-100 are rejected."""
        with self.app.app_context():
            valid, errors, _ = validate_student_data(
                "John Doe", "REG102", "Computer Science", "john@university.edu", "105.0"
            )
            self.assertFalse(valid)
            self.assertTrue(any("between 0.0 and 100.0" in e for e in errors))

    def test_validate_student_data_duplicate_register_number(self):
        """Verify duplicate register number is caught and rejected."""
        with self.app.app_context():
            valid, errors, _ = validate_student_data(
                "Another Student", "REG999", "Computer Science", "another@test.edu", "75.0"
            )
            self.assertFalse(valid)
            self.assertTrue(any("already exists" in e for e in errors))

    def test_validate_student_data_invalid_department(self):
        """Verify unknown department is rejected against whitelist (B5)."""
        with self.app.app_context():
            valid, errors, _ = validate_student_data(
                "John Doe", "REG103", "Astronomy & Astrophysics", "john@test.edu", "85.0"
            )
            self.assertFalse(valid)
            self.assertTrue(any("Invalid department" in e for e in errors))

    # -------------------------------------------------------------------------
    # Student CRUD Tests
    # -------------------------------------------------------------------------
    def test_create_and_view_student(self):
        """Test adding a student and viewing their profile."""
        self.login("staff", "Staff@123")

        # Create student
        add_res = self.client.post('/students/add', data={
            'name': 'Pooja Hegde',
            'register_number': 'REG505',
            'department': 'Information Technology',
            'email': 'pooja@example.edu',
            'marks': '88.5'
        }, follow_redirects=True)
        self.assertEqual(add_res.status_code, 200)
        self.assertIn(b"Pooja Hegde", add_res.data)

        # Verify in database
        with self.app.app_context():
            student = query_db("SELECT * FROM students WHERE register_number = 'REG505'", one=True)
            self.assertIsNotNone(student)
            self.assertEqual(student['marks'], 88.5)

    def test_edit_student(self):
        """Test updating student marks and department."""
        self.login("staff", "Staff@123")

        with self.app.app_context():
            student = query_db("SELECT id FROM students WHERE register_number = 'REG999'", one=True)
            student_id = student['id']

        edit_res = self.client.post(f'/students/{student_id}/edit', data={
            'name': 'Test Student Updated',
            'register_number': 'REG999',
            'department': 'Data Science',
            'email': 'updated@test.edu',
            'marks': '96.0'
        }, follow_redirects=True)
        self.assertEqual(edit_res.status_code, 200)

        with self.app.app_context():
            updated = query_db("SELECT * FROM students WHERE id = ?", (student_id,), one=True)
            self.assertEqual(updated['name'], 'Test Student Updated')
            self.assertEqual(updated['marks'], 96.0)
            self.assertEqual(updated['department'], 'Data Science')

    def test_delete_student_as_admin(self):
        """Test admin deleting a student record."""
        self.login("admin", "Admin@123")

        with self.app.app_context():
            student = query_db("SELECT id FROM students WHERE register_number = 'REG999'", one=True)
            student_id = student['id']

        del_res = self.client.post(f'/students/{student_id}/delete', follow_redirects=True)
        self.assertEqual(del_res.status_code, 200)

        with self.app.app_context():
            active_student = query_db("SELECT id FROM students WHERE id = ? AND deleted_at IS NULL", (student_id,), one=True)
            self.assertIsNone(active_student)
            soft_deleted = query_db("SELECT deleted_at FROM students WHERE id = ?", (student_id,), one=True)
            self.assertIsNotNone(soft_deleted['deleted_at'])

    # -------------------------------------------------------------------------
    # Search and Filtering Tests
    # -------------------------------------------------------------------------
    def test_student_search(self):
        """Test search query matching name or register number."""
        self.login("staff", "Staff@123")
        res = self.client.get('/students?q=REG999')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Test Student", res.data)

        res_miss = self.client.get('/students?q=NONEXISTENT')
        self.assertEqual(res_miss.status_code, 200)
        self.assertIn(b"No matching students found", res_miss.data)

    # -------------------------------------------------------------------------
    # CSV Export Test
    # -------------------------------------------------------------------------
    def test_export_csv(self):
        """Test CSV download route."""
        self.login("staff", "Staff@123")
        res = self.client.get('/students/export')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, 'text/csv')
        self.assertIn(b"Register Number,Name,Department,Email,Marks,Grade", res.data)
        self.assertIn(b"REG999", res.data)

    # -------------------------------------------------------------------------
    # Advanced Features: PDF Marksheet, CSV Import & Password Change
    # -------------------------------------------------------------------------
    def test_pdf_marksheet_download(self):
        """Test generating and downloading student PDF marksheet."""
        self.login("staff", "Staff@123")
        with self.app.app_context():
            student = query_db("SELECT id FROM students WHERE register_number = 'REG999'", one=True)
            student_id = student['id']

        res = self.client.get(f'/students/{student_id}/pdf')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, 'application/pdf')
        # Check PDF header magic bytes
        self.assertTrue(res.data.startswith(b'%PDF-'))

    def test_csv_template_download(self):
        """Test downloading CSV import template."""
        self.login("staff", "Staff@123")
        res = self.client.get('/students/template.csv')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, 'text/csv')
        self.assertIn(b"Name,Register Number,Department,Email,Marks", res.data)

    def test_bulk_import_students(self):
        """Test importing student records in bulk via CSV."""
        self.login("staff", "Staff@123")
        csv_content = (
            "Name,Register Number,Department,Email,Marks\n"
            "Deepak Verma,REG2024888,Computer Science,deepak@test.edu,82.0\n"
            "Anil Kumble,REG2024889,Data Science,anil@test.edu,90.5\n"
        )
        csv_bytes = io.BytesIO(csv_content.encode('utf-8'))

        res = self.client.post('/students/import', data={
            'csv_file': (csv_bytes, 'students.csv')
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        with self.app.app_context():
            s1 = query_db("SELECT * FROM students WHERE register_number = 'REG2024888'", one=True)
            s2 = query_db("SELECT * FROM students WHERE register_number = 'REG2024889'", one=True)
            self.assertIsNotNone(s1)
            self.assertIsNotNone(s2)
            self.assertEqual(s1['name'], 'Deepak Verma')
            self.assertEqual(s2['marks'], 90.5)

    def test_change_password(self):
        """Test changing user password and logging in with the new password."""
        self.login("staff", "Staff@123")

        # Wrong current password fails
        res_fail = self.client.post('/change-password', data={
            'current_password': 'WrongPassword',
            'new_password': 'NewPassword@123',
            'confirm_password': 'NewPassword@123'
        }, follow_redirects=True)
        self.assertIn(b"incorrect", res_fail.data)

        # Successful password update
        res_success = self.client.post('/change-password', data={
            'current_password': 'Staff@123',
            'new_password': 'NewPassword@123',
            'confirm_password': 'NewPassword@123'
        }, follow_redirects=True)
        self.assertEqual(res_success.status_code, 200)
        self.assertIn(b"updated successfully", res_success.data)

        # Sign out and sign in with new password
        self.logout()
        res_login_new = self.login("staff", "NewPassword@123")
        self.assertEqual(res_login_new.status_code, 200)
        self.assertIn(b"Academic Dashboard", res_login_new.data)

    # -------------------------------------------------------------------------
    # 4 Killer Features Tests: Student Portal, CGPA, Attendance, Email
    # -------------------------------------------------------------------------
    def test_student_portal_login_and_access(self):
        """Test student role login redirects to dedicated Student Portal."""
        with self.app.app_context():
            s = query_db("SELECT id FROM students WHERE register_number = 'REG999'", one=True)
            stud_pwd = generate_password_hash("Student@123", method="pbkdf2:sha256")
            execute_db(
                "INSERT INTO users (username, password_hash, role, full_name, email, student_id) VALUES (?, ?, ?, ?, ?, ?)",
                ("reg999", stud_pwd, "student", "Test Student", "test@test.edu", s['id'])
            )
            # Add attendance
            execute_db(
                "INSERT INTO student_attendance (student_id, total_classes, attended_classes, attendance_percentage) VALUES (?, ?, ?, ?)",
                (s['id'], 60, 54, 90.0)
            )

        res = self.login("reg999", "Student@123")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Student Academic Portal", res.data)
        self.assertIn(b"REG999", res.data)
        self.assertIn(b"90.0%", res.data)

    def test_attendance_update_and_shortage_alert(self):
        """Test staff updating attendance to below 75% triggers shortage alert on portal."""
        self.login("staff", "Staff@123")
        with self.app.app_context():
            s = query_db("SELECT id FROM students WHERE register_number = 'REG999'", one=True)
            stud_id = s['id']

        # Update attendance to 42 out of 60 (70.0% -> Shortage Alert)
        res_update = self.client.post(f"/students/{stud_id}/attendance", data={
            'total_classes': '60',
            'attended_classes': '42'
        }, follow_redirects=True)
        self.assertEqual(res_update.status_code, 200)

        with self.app.app_context():
            att = query_db("SELECT * FROM student_attendance WHERE student_id = ?", (stud_id,), one=True)
            self.assertIsNotNone(att)
            self.assertEqual(att['attendance_percentage'], 70.0)

        # Login as student and check shortage banner appears
        self.logout()
        with self.app.app_context():
            stud_pwd = generate_password_hash("Student@123", method="pbkdf2:sha256")
            execute_db(
                "INSERT OR REPLACE INTO users (username, password_hash, role, full_name, email, student_id) VALUES (?, ?, ?, ?, ?, ?)",
                ("reg999", stud_pwd, "student", "Test Student", "test@test.edu", stud_id)
            )
        res_portal = self.login("reg999", "Student@123")
        self.assertIn(b"ATTENDANCE SHORTAGE NOTICE", res_portal.data)

    def test_add_subject_and_cgpa_calculation(self):
        """Test adding semester subjects and automated CGPA recalculation."""
        self.login("staff", "Staff@123")
        with self.app.app_context():
            s = query_db("SELECT id FROM students WHERE register_number = 'REG999'", one=True)
            stud_id = s['id']

        # Add Course 1: Credits 4, Marks 92.0 -> GP 10.0 (O)
        res1 = self.client.post(f"/students/{stud_id}/add-subject", data={
            'semester': '6',
            'subject_code': 'CS601',
            'subject_name': 'Cloud Computing Systems',
            'credits': '4',
            'marks': '92.0'
        }, follow_redirects=True)
        self.assertEqual(res1.status_code, 200)

        # Add Course 2: Credits 4, Marks 74.0 -> GP 8.0 (A)
        res2 = self.client.post(f"/students/{stud_id}/add-subject", data={
            'semester': '6',
            'subject_code': 'CS602',
            'subject_name': 'Distributed Systems',
            'credits': '4',
            'marks': '74.0'
        }, follow_redirects=True)
        self.assertEqual(res2.status_code, 200)

        # Expected CGPA: (4*10 + 4*8) / 8 = 72 / 8 = 9.0
        with self.app.app_context():
            updated_stud = query_db("SELECT cgpa FROM students WHERE id = ?", (stud_id,), one=True)
            self.assertEqual(updated_stud['cgpa'], 9.0)

    def test_email_marksheet_endpoint(self):
        """Test dispatching marksheet PDF via email simulation."""
        self.login("staff", "Staff@123")
        with self.app.app_context():
            s = query_db("SELECT id FROM students WHERE register_number = 'REG999'", one=True)
            stud_id = s['id']

        res = self.client.post(f"/students/{stud_id}/email-marksheet", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Marksheet sent to", res.data)

    def test_pdf_marksheet_stream_size(self):
        """Test generating and downloading official marksheet PDF with valid size."""
        self.login("staff", "Staff@123")
        with self.app.app_context():
            s = query_db("SELECT id FROM students WHERE register_number = 'REG999'", one=True)
            stud_id = s['id']

        res = self.client.get(f"/students/{stud_id}/pdf")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "application/pdf")
        self.assertTrue(len(res.data) > 1000)

    # -------------------------------------------------------------------------
    # Technical Improvements Tests: QR Verification, REST API, Swagger UI
    # -------------------------------------------------------------------------
    def test_verify_marksheet_endpoint(self):
        """Test public marksheet verification portal with cryptographic token."""
        # 1. Valid Student with Token
        res_valid = self.client.get("/verify/verify_token_reg999_sample")
        self.assertEqual(res_valid.status_code, 200)
        self.assertIn(b"OFFICIALLY VERIFIED", res_valid.data)
        self.assertIn(b"REG999", res_valid.data)

        # 2. Non-existent Token
        res_invalid = self.client.get("/verify/NONEXISTENT999TOKEN")
        self.assertEqual(res_invalid.status_code, 404)
        self.assertIn(b"Digital Credential Not Found", res_invalid.data)

    def test_rest_api_health_and_docs(self):
        """Test REST API health check, OpenAPI spec, and Swagger documentation."""
        # Health check
        res_health = self.client.get("/api/v1/health")
        self.assertEqual(res_health.status_code, 200)
        json_data = res_health.get_json()
        self.assertEqual(json_data['status'], 'healthy')

        # OpenAPI spec
        res_spec = self.client.get("/api/v1/openapi.json")
        self.assertEqual(res_spec.status_code, 200)
        spec = res_spec.get_json()
        self.assertEqual(spec['openapi'], '3.0.0')

        # Swagger UI page
        res_docs = self.client.get("/api/docs")
        self.assertEqual(res_docs.status_code, 200)
        self.assertIn(b"SwaggerUIBundle", res_docs.data)

    def test_rest_api_students_crud(self):
        """Test RESTful student endpoints (list, get, create) with authenticated session."""
        self.login("admin", "Admin@123")
        # 1. List students
        res_list = self.client.get("/api/v1/students")
        self.assertEqual(res_list.status_code, 200)
        data = res_list.get_json()
        self.assertTrue(data['total'] >= 1)
        self.assertIsInstance(data['students'], list)

        # 2. Get single student
        student_id = data['students'][0]['id']
        res_get = self.client.get(f"/api/v1/students/{student_id}")
        self.assertEqual(res_get.status_code, 200)
        dossier = res_get.get_json()
        self.assertIn('student', dossier)
        self.assertIn('academic_standing', dossier)

        # 3. Create student via JSON
        res_create = self.client.post("/api/v1/students", json={
            'name': 'API Test Student',
            'register_number': 'REGAPI2026',
            'department': 'Data Science',
            'email': 'api.student@example.edu',
            'marks': 89.5
        })
        self.assertEqual(res_create.status_code, 201)
        self.assertIn('id', res_create.get_json())

    def test_rate_limiter_blocks_abuse(self):
        """Test rate limiter blocks requests after exceeding threshold when enabled."""
        self.app.config['TEST_RATE_LIMITER'] = True
        try:
            for _ in range(10):
                self.client.post('/login', data={'username': 'attacker', 'password': '123'})
            res_blocked = self.client.post('/login', data={'username': 'attacker', 'password': '123'})
            self.assertEqual(res_blocked.status_code, 429)
            self.assertIn(b"429", res_blocked.data)
            self.assertIn(b"too many login attempts", res_blocked.data.lower())
        finally:
            self.app.config['TEST_RATE_LIMITER'] = False


if __name__ == '__main__':
    unittest.main()



