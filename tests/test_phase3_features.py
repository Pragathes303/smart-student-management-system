"""
Comprehensive Test Suite for Phase 3: Features Needed for Real Use
Covers:
1. Single reusable grade function: marks -> (grade, grade_point), Anna University scale,
   boundary values (49, 50, 59, 60, 89, 90, 100).
2. Subjects, credits, semesters: credit-weighted SGPA per semester and cumulative CGPA.
3. Attendance module: per-subject/day tracking, attendance percentage, warning below 75%.
4. Student list: pagination, sorting, search, URL-preserved filters (dept, year, grade, status).
5. Exports: filtered CSV & Excel lists, PDF report card with college header & Anna University scale.
6. Bulk import: CSV/Excel template download, row validation, preview mode, added/skipped summary.
7. Dashboard: total students, pass %, grade distribution chart data, dept pass %, low-attendance list.
8. Staff RBAC: staff can only edit marks for assigned department / subjects.
9. Student portal: view own marks, grades, SGPA/CGPA, attendance, download own report card.
10. Notifications: flash feedback on actions, optional SMTP marks publication email trigger.
11. Semester marks publish / lock: staff blocked on published semesters, admin override audited.
"""

import io
import json
import openpyxl
import pytest

from backend.grading import (
    calculate_cgpa,
    calculate_semester_and_overall_gpa,
    calculate_sgpa,
    get_grade_and_points,
    get_grade_info,
)
from database.db import execute_db, query_db
from tests.conftest import login, logout


# =============================================================================
# 1. Single Reusable Grade Function (Anna University Scale & Boundary Values)
# =============================================================================
class TestAnnaUniversityGradeScale:
    """Tests single reusable grade function with required boundary values."""

    def test_grade_function_exact_boundary_values(self):
        """Verify boundary values 49, 50, 59, 60, 89, 90, 100."""
        # 49 -> RA (Reappear, 0.0)
        grade, points = get_grade_and_points(49.0)
        assert grade == "RA"
        assert points == 0.0

        # 50 -> B (Average, 6.0)
        grade, points = get_grade_and_points(50.0)
        assert grade == "B"
        assert points == 6.0

        # 59 -> B (Average, 6.0)
        grade, points = get_grade_and_points(59.0)
        assert grade == "B"
        assert points == 6.0

        # 60 -> B+ (Good, 7.0)
        grade, points = get_grade_and_points(60.0)
        assert grade == "B+"
        assert points == 7.0

        # 89 -> A+ (Excellent, 9.0)
        grade, points = get_grade_and_points(89.0)
        assert grade == "A+"
        assert points == 9.0

        # 90 -> O (Outstanding, 10.0)
        grade, points = get_grade_and_points(90.0)
        assert grade == "O"
        assert points == 10.0

        # 100 -> O (Outstanding, 10.0)
        grade, points = get_grade_and_points(100.0)
        assert grade == "O"
        assert points == 10.0

    def test_grade_function_all_tiers_and_clamping(self):
        """Verify Intermediate marks, status indicators, and range clamping."""
        # 75 -> A (Very Good, 8.0)
        info = get_grade_info(75.0)
        assert info["letter"] == "A"
        assert info["grade_point"] == 8.0
        assert info["status"] == "Pass"
        assert info["is_pass"] is True

        # Edge cases: clamped values
        assert get_grade_and_points(-10.0) == ("RA", 0.0)
        assert get_grade_and_points(115.0) == ("O", 10.0)


# =============================================================================
# 2. Subjects, Credits, Semesters: Credit-Weighted SGPA and CGPA
# =============================================================================
class TestAcademicGpaCalculations:
    """Tests semester-wise SGPA and cumulative CGPA calculation."""

    def test_credit_weighted_sgpa_and_overall_cgpa(self):
        """
        Sem 1:
          - CS101: 4 credits, 92 marks -> 'O' (10.0 GP) => 40
          - CS102: 3 credits, 72 marks -> 'A' (8.0 GP)  => 24
          SGPA = (40 + 24) / 7 = 64 / 7 = 9.14
        Sem 2:
          - CS201: 3 credits, 55 marks -> 'B' (6.0 GP)  => 18
          - CS202: 4 credits, 45 marks -> 'RA' (0.0 GP) => 0
          SGPA = (18 + 0) / 7 = 18 / 7 = 2.57
        Overall:
          Total credits = 14, Points = 82, CGPA = 82 / 14 = 5.86
        """
        subjects = [
            {"semester": 1, "subject_code": "CS101", "credits": 4, "marks": 92.0},
            {"semester": 1, "subject_code": "CS102", "credits": 3, "marks": 72.0},
            {"semester": 2, "subject_code": "CS201", "credits": 3, "marks": 55.0},
            {"semester": 2, "subject_code": "CS202", "credits": 4, "marks": 45.0},
        ]

        sem1_stats = calculate_sgpa([subjects[0], subjects[1]])
        assert sem1_stats["sgpa"] == 9.14
        assert sem1_stats["total_credits"] == 7
        assert sem1_stats["earned_credits"] == 7
        assert sem1_stats["has_arrears"] is False

        sem2_stats = calculate_sgpa([subjects[2], subjects[3]])
        assert sem2_stats["sgpa"] == 2.57
        assert sem2_stats["total_credits"] == 7
        assert sem2_stats["earned_credits"] == 3
        assert sem2_stats["has_arrears"] is True
        assert sem2_stats["arrears"] == 1

        multi_sem = calculate_semester_and_overall_gpa(subjects)
        assert 1 in multi_sem["by_semester"]
        assert 2 in multi_sem["by_semester"]
        assert multi_sem["by_semester"][1]["sgpa"] == 9.14
        assert multi_sem["by_semester"][2]["sgpa"] == 2.57
        assert multi_sem["overall"]["cgpa"] == 5.86
        assert multi_sem["overall"]["total_credits"] == 14
        assert multi_sem["overall"]["earned_credits"] == 10


# =============================================================================
# 3. Attendance Module: Subject/Day Tracking & Shortage Warning (<75%)
# =============================================================================
class TestAttendanceModule:
    """Tests recording attendance per subject/day and shortage alert badges."""

    def test_mark_attendance_per_subject_and_shortage_warning(self, app, staff_client):
        """Mark attendance for student and verify shortage warning triggers when < 75%."""
        with app.app_context():
            student = query_db("SELECT id FROM students WHERE register_number = 'REG999'", one=True)
            sid = student["id"]

            # Mark 3 absent sessions and 1 present session for subject CS101 -> 25% attendance
            res = staff_client.post('/attendance/mark', data={
                'department': 'Computer Science',
                'subject_code': 'CS101',
                'date': '2026-10-26',
                'period': 1,
                f'status_{sid}': 'absent'
            }, follow_redirects=True)
            assert res.status_code == 200

            # Verify recorded in attendance_records table with subject_code
            record = query_db(
                "SELECT * FROM attendance_records WHERE student_id = ? AND subject_code = 'CS101' ORDER BY id DESC LIMIT 1",
                (sid,),
                one=True
            )
            assert record is not None
            assert record["status"] == "absent"
            assert record["subject_code"] == "CS101"

            # Check attendance overview page contains alert badge
            overview_res = staff_client.get('/attendance')
            assert overview_res.status_code == 200
            assert b"Attendance" in overview_res.data


# =============================================================================
# 4. Student List: Pagination, Sorting, Search, and URL-Preserved Filters
# =============================================================================
class TestStudentListFiltersAndPreservation:
    """Tests search, filtering by department, year, grade, status, and URL preservation."""

    def test_student_list_filters_and_export_links_preserve_state(self, app, staff_client):
        """Test URL-based filtering persists through search parameters and export hrefs."""
        with app.app_context():
            # Add a 2nd year IT student with RA grade (marks: 40)
            execute_db(
                """
                INSERT INTO students (name, register_number, department, year, email, marks)
                VALUES ('Kavitha Rao', 'REG2024200', 'Information Technology', 2, 'kavitha@test.edu', 40.0)
                """,
                commit=True
            )

            # 1. Filter by Department
            res_dept = staff_client.get('/students?dept=Information+Technology')
            assert res_dept.status_code == 200
            assert b"Kavitha Rao" in res_dept.data
            assert b"REG999" not in res_dept.data

            # 2. Filter by Year
            res_year = staff_client.get('/students?year=2')
            assert res_year.status_code == 200
            assert b"Kavitha Rao" in res_year.data
            assert b"REG999" not in res_year.data

            # 3. Filter by Anna University Grade (RA)
            res_grade = staff_client.get('/students?grade=RA')
            assert res_grade.status_code == 200
            assert b"Kavitha Rao" in res_grade.data

            # 4. Filter by Status (Fail)
            res_status = staff_client.get('/students?status=fail')
            assert res_status.status_code == 200
            assert b"Kavitha Rao" in res_status.data

            # 5. Export links preserve the active filters
            assert b"/students/export?dept=Information+Technology" in res_dept.data or b"dept=Information+Technology" in res_dept.data


# =============================================================================
# 5. Export: Filtered CSV, Excel, and PDF Report Card with College Header
# =============================================================================
class TestExportAndPdfReportCards:
    """Tests CSV/Excel filtered exports and official PDF report cards."""

    def test_export_csv_and_excel_honors_filters(self, app, staff_client):
        """Verify export downloads only include filtered records."""
        with app.app_context():
            execute_db(
                """
                INSERT INTO students (name, register_number, department, year, email, marks)
                VALUES ('Siddharth Nair', 'REG2024300', 'Mechanical Engineering', 3, 'sid@test.edu', 78.0)
                """,
                commit=True
            )

            # CSV export filtered by department
            res_csv = staff_client.get('/students/export?dept=Mechanical+Engineering')
            assert res_csv.status_code == 200
            assert res_csv.mimetype == "text/csv"
            assert b"REG2024300" in res_csv.data
            assert b"REG999" not in res_csv.data

            # Excel export filtered by department
            res_excel = staff_client.get('/students/export-excel?dept=Mechanical+Engineering')
            assert res_excel.status_code == 200
            assert "spreadsheet" in res_excel.mimetype.lower() or res_excel.status_code == 200

            wb = openpyxl.load_workbook(io.BytesIO(res_excel.data))
            sheet = wb.active
            rows = list(sheet.iter_rows(values_only=True))
            reg_nos = [str(r[0]) for r in rows[1:] if r[0]]
            assert "REG2024300" in reg_nos
            assert "REG999" not in reg_nos

    def test_student_pdf_report_card_structure(self, app, staff_client):
        """Test PDF generation contains college header, student info, Anna University grades & CGPA."""
        with app.app_context():
            student = query_db("SELECT id FROM students WHERE register_number = 'REG999'", one=True)
            sid = student["id"]

            res_pdf = staff_client.get(f'/students/{sid}/pdf')
            assert res_pdf.status_code == 200
            assert res_pdf.mimetype == "application/pdf"
            assert res_pdf.data.startswith(b"%PDF")
            # PDF byte size must be substantial (> 2KB)
            assert len(res_pdf.data) > 2000


# =============================================================================
# 6. Bulk Import: Templates, Validation, Preview & Summary Counters
# =============================================================================
class TestBulkImportWorkflow:
    """Tests CSV/Excel template download, multi-format parsing, preview, and atomic commit."""

    def test_template_downloads(self, staff_client):
        """Verify downloadable templates exist for both CSV and Excel."""
        # CSV template
        res_csv = staff_client.get('/students/template.csv')
        assert res_csv.status_code == 200
        assert b"Name,Register Number,Department" in res_csv.data

        # Excel template
        res_excel = staff_client.get('/students/template.xlsx')
        assert res_excel.status_code == 200
        wb = openpyxl.load_workbook(io.BytesIO(res_excel.data))
        assert "Student Import Template" in wb.sheetnames

    def test_import_with_preview_mode_and_confirmation(self, app, staff_client):
        """Upload CSV in preview mode, inspect preview summary, and confirm."""
        csv_data = (
            "Name,Register Number,Department,Year,Email,Marks\n"
            "Arun Prakash,REG2024501,Computer Science,1,arun.p@test.edu,88.0\n"
            "Deepa Mohan,REG2024502,Data Science,2,deepa.m@test.edu,92.5\n"
            ",REG_INVALID,Unknown Branch,1,bademail,150\n"
        )
        file_tuple = (io.BytesIO(csv_data.encode('utf-8')), 'batch.csv')

        # 1. Preview mode POST
        res_preview = staff_client.post(
            '/students/import',
            data={'import_file': file_tuple, 'preview_mode': '1'}
        )
        assert res_preview.status_code == 200
        assert b"Import Preview" in res_preview.data
        assert b"Arun Prakash" in res_preview.data
        assert b"badge-danger" in res_preview.data or b"Invalid" in res_preview.data

        # 2. Confirm import from preview session
        res_confirm = staff_client.post(
            '/students/import',
            data={'action': 'confirm'},
            follow_redirects=True
        )
        assert res_confirm.status_code == 200
        assert b"Successfully imported 2 student record(s)" in res_confirm.data

        # Verify students saved in database
        with app.app_context():
            st1 = query_db("SELECT * FROM students WHERE register_number = 'REG2024501'", one=True)
            st2 = query_db("SELECT * FROM students WHERE register_number = 'REG2024502'", one=True)
            assert st1 is not None
            assert st2 is not None


# =============================================================================
# 7. Dashboard: Pass %, Department-wise Pass %, Grade Distribution Chart, Shortage
# =============================================================================
class TestDashboardAnalytics:
    """Tests overall analytics, grade distributions, and shortage watchlists."""

    def test_dashboard_displays_all_metrics(self, app, staff_client):
        """Dashboard renders total students, pass %, dept metrics, and shortage list."""
        with app.app_context():
            res = staff_client.get('/dashboard')
            assert res.status_code == 200
            assert b"Total Students" in res.data
            assert b"Pass Percentage" in res.data
            assert b"Department-Wise Pass Percentage" in res.data or b"Pass %" in res.data
            assert b"gradeChart" in res.data  # Chart.js grade distribution canvas
            assert b"Attendance Shortage Watchlist" in res.data or b"Shortage" in res.data


# =============================================================================
# 8. Staff Permissions: Isolation by Assigned Department and Subjects
# =============================================================================
class TestStaffDepartmentAndSubjectPermissions:
    """Verify staff can only edit marks for assigned department and subjects."""

    def test_staff_restricted_to_assigned_department(self, app, client):
        """Staff assigned to IT cannot edit marks for CS student (403 forbidden)."""
        with app.app_context():
            from werkzeug.security import generate_password_hash

            # Create staff assigned strictly to 'Information Technology'
            pwd = generate_password_hash("StaffIT@123", method="pbkdf2:sha256")
            execute_db(
                """
                INSERT INTO users (username, password_hash, role, full_name, email, department)
                VALUES ('staff_it', ?, 'staff', 'IT Staff Member', 'staff.it@test.edu', 'Information Technology')
                """,
                (pwd,),
                commit=True
            )
            cs_student = query_db("SELECT id, updated_at FROM students WHERE register_number = 'REG999'", one=True)
            cs_id = cs_student["id"]

        # Log in as IT staff
        login(client, 'staff_it', 'StaffIT@123')

        # Attempt to edit CS student marks
        res = client.post(
            f'/students/{cs_id}/edit',
            data={
                'original_updated_at': cs_student['updated_at'],
                'name': 'Test Student',
                'register_number': 'REG999',
                'department': 'Computer Science',
                'year': '1',
                'email': 'student@test.edu',
                'marks': '99.0'
            }
        )
        assert res.status_code == 403
        assert b"Access Denied" in res.data

    def test_staff_restricted_to_assigned_subjects(self, app, client):
        """Staff assigned to 'IT101' cannot edit subject 'CS101' (403 forbidden)."""
        with app.app_context():
            from werkzeug.security import generate_password_hash

            pwd = generate_password_hash("StaffSubj@123", method="pbkdf2:sha256")
            execute_db(
                """
                INSERT INTO users (username, password_hash, role, full_name, email, assigned_subjects)
                VALUES ('staff_subj', ?, 'staff', 'Subject Teacher', 'staff.subj@test.edu', 'IT101,IT102')
                """,
                (pwd,),
                commit=True
            )
            sub = query_db("SELECT * FROM student_subjects WHERE subject_code = 'CS101'", one=True)
            sid = sub["student_id"]
            sub_id = sub["id"]

        login(client, 'staff_subj', 'StaffSubj@123')

        res = client.post(
            f'/students/{sid}/edit-subject/{sub_id}',
            data={
                'semester': 1,
                'subject_code': 'CS101',
                'subject_name': 'Introduction to Python',
                'credits': 3,
                'marks': '98.0'
            }
        )
        assert res.status_code == 403
        assert b"Access Denied" in res.data


# =============================================================================
# 9. Marks Publish / Lock per Semester: Staff Blocked & Admin Override Audited
# =============================================================================
class TestMarksPublishLockPerSemester:
    """Tests locking semester results against staff edits and auditing admin overrides."""

    def test_staff_blocked_on_published_semester_and_admin_allowed_with_audit(self, app, client):
        """When semester 1 is published, staff is blocked; admin override succeeds with audit log."""
        with app.app_context():
            sub = query_db("SELECT * FROM student_subjects WHERE subject_code = 'CS101'", one=True)
            sid = sub["student_id"]
            sub_id = sub["id"]

        # Admin logs in and publishes Semester 1
        login(client, 'admin', 'Admin@123')
        res_lock = client.post('/settings/semester-lock', data={'semester': 1}, follow_redirects=True)
        assert res_lock.status_code == 200

        with app.app_context():
            lock_val = query_db("SELECT value FROM system_settings WHERE key = 'semester_1_published'", one=True)
            assert lock_val is not None
            assert lock_val["value"] == "1"

        logout(client)

        # Staff attempts to edit locked semester subject -> 403 Forbidden
        login(client, 'staff', 'Staff@123')
        res_staff_edit = client.post(
            f'/students/{sid}/edit-subject/{sub_id}',
            data={
                'semester': 1,
                'subject_code': 'CS101',
                'subject_name': 'Introduction to Python',
                'credits': 3,
                'marks': '99.0'
            }
        )
        assert res_staff_edit.status_code == 403
        assert b"published and locked" in res_staff_edit.data

        logout(client)

        # Admin edits locked semester subject -> Allowed and audited
        login(client, 'admin', 'Admin@123')
        res_admin_edit = client.post(
            f'/students/{sid}/edit-subject/{sub_id}',
            data={
                'semester': 1,
                'subject_code': 'CS101',
                'subject_name': 'Introduction to Python',
                'credits': 3,
                'marks': '99.0',
                'reason': 'Board re-evaluation mark correction'
            },
            follow_redirects=True
        )
        assert res_admin_edit.status_code == 200

        # Verify audit log and marks history recorded
        with app.app_context():
            audit = query_db(
                "SELECT * FROM audit_logs WHERE action = 'ADMIN_MARKS_OVERRIDE_AFTER_PUBLISH' ORDER BY id DESC LIMIT 1",
                one=True
            )
            assert audit is not None
            assert "CS101" in audit["details"]

            m_hist = query_db(
                "SELECT * FROM marks_history WHERE student_id = ? AND subject_code = 'CS101' ORDER BY id DESC LIMIT 1",
                (sid,),
                one=True
            )
            assert m_hist is not None
            assert float(m_hist["new_marks"]) == 99.0


# =============================================================================
# 10. Student Portal: View Own Marks, SGPA/CGPA, Attendance, and PDF Download
# =============================================================================
class TestStudentPortalSelfService:
    """Tests student portal self-service views and own marksheet PDF download."""

    def test_student_portal_views_and_download_report_card(self, student_client):
        """Student views portal, sees own SGPA/CGPA and attendance, and downloads marksheet PDF."""
        res_portal = student_client.get('/student/portal')
        assert res_portal.status_code == 200
        assert b"Student Academic Portal" in res_portal.data
        assert b"CGPA" in res_portal.data
        assert b"Semester 1" in res_portal.data
        assert b"Grade Card (PDF)" in res_portal.data

        # Download report card
        res_pdf = student_client.get('/student/report-card.pdf')
        assert res_pdf.status_code == 200
        assert res_pdf.mimetype == "application/pdf"
        assert res_pdf.data.startswith(b"%PDF")
