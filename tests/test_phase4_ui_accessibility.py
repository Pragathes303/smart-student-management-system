"""
Comprehensive Test Suite for Phase 4: UI/UX and Accessibility
Covers:
1. Custom 403, 404, and 500 error pages using existing layout.
2. Empty states ("No students found" with a button to add one) and loading states.
3. Mobile hamburger navigation menu and drawer in base layout.
4. Keyboard accessibility: skip-to-content, labeled inputs, proper aria attributes.
5. Styled accessible confirmation modal (replacing browser confirm/alert).
6. Print-friendly report card page for both staff/admin and student portal.
"""

from flask import abort

from backend.utils import map_errors_to_fields
from database.db import execute_db, query_db


# =============================================================================
# 1. Custom 403, 404, 500 Pages Using Existing Layout
# =============================================================================
class TestCustomErrorPages:
    """Verify custom 403, 404, 500 error pages inherit base layout and return correct codes."""

    def test_custom_403_page(self, client):
        """Test 403 forbidden error page."""
        # Unauthenticated access to staff route triggers 403 or redirect; test explicit 403
        @client.application.route('/_test_custom_403')
        def _trigger_403():
            abort(403, description="Access Forbidden: Administrative privileges required.")

        res = client.get('/_test_custom_403')
        assert res.status_code == 403
        assert b"403" in res.data
        assert b"Access Forbidden" in res.data
        assert b"navbar" in res.data or b"StudentMS" in res.data  # uses base layout

    def test_custom_404_page(self, client):
        """Test 404 page not found error page."""
        res = client.get('/this-url-definitely-does-not-exist-404')
        assert res.status_code == 404
        assert b"404" in res.data
        assert b"Page Not Found" in res.data
        assert b"StudentMS" in res.data  # uses base layout

    def test_custom_500_page(self, client):
        """Test 500 internal server error page."""
        @client.application.route('/_test_custom_500')
        def _trigger_500():
            abort(500, description="Internal Server Error: Database deadlock occurred.")

        res = client.get('/_test_custom_500')
        assert res.status_code == 500
        assert b"500" in res.data
        assert b"Internal Server Error" in res.data
        assert b"StudentMS" in res.data  # uses base layout


# =============================================================================
# 2. Empty States ("No students found" with button to add one)
# =============================================================================
class TestEmptyStates:
    """Verify empty state displays when no records match, including add student button."""

    def test_student_list_empty_state_with_button(self, app, staff_client):
        """When search filter yields zero results, displays empty state and add student button."""
        res = staff_client.get('/students?q=NONEXISTENT_QUERY_12345')
        assert res.status_code == 200
        assert b"No students found" in res.data
        assert b"+ Add New Student" in res.data or b"Register New Student" in res.data
        assert b"/students/add" in res.data

    def test_trash_bin_empty_state(self, app, admin_client):
        """When trash bin is empty, displays clean empty state message."""
        with app.app_context():
            # Ensure no soft-deleted records
            execute_db("UPDATE students SET deleted_at = NULL WHERE deleted_at IS NOT NULL", commit=True)

        res = admin_client.get('/students/trash')
        assert res.status_code == 200
        assert b"Trash Bin is Empty" in res.data


# =============================================================================
# 3. Validation Messages & Field Error Mapping
# =============================================================================
class TestFieldValidationMessages:
    """Verify contextual error messages next to input fields."""

    def test_map_errors_to_fields_utility(self):
        """Verify raw error messages are accurately mapped to form field names."""
        raw_errors = [
            "Student Name must be between 2 and 80 characters.",
            "Register Number is required.",
            "Please provide a valid email address.",
            "Marks must be between 0 and 100.",
            "Invalid department 'Magic'.",
            "Phone number must contain between 7 and 15 digits."
        ]
        mapped = map_errors_to_fields(raw_errors)
        assert mapped['name'] == raw_errors[0]
        assert mapped['register_number'] == raw_errors[1]
        assert mapped['email'] == raw_errors[2]
        assert mapped['marks'] == raw_errors[3]
        assert mapped['department'] == raw_errors[4]
        assert mapped['phone'] == raw_errors[5]

    def test_add_student_displays_inline_validation_errors(self, staff_client):
        """Submitting invalid data displays inline error messages and retains form inputs."""
        res = staff_client.post(
            '/students/add',
            data={
                'name': '',
                'register_number': '??BAD_REG??',
                'department': 'Computer Science',
                'year': '1',
                'email': 'not-an-email',
                'marks': '150'
            }
        )
        assert res.status_code == 200
        assert b"field-error-message" in res.data
        assert b"aria-invalid" in res.data


# =============================================================================
# 4. Accessibility: Skip Link, Labels, Aria Attributes, and Modal
# =============================================================================
class TestAccessibilityAndModal:
    """Verify accessibility elements and styled confirmation modal in layout."""

    def test_base_layout_accessibility_features(self, staff_client):
        """Verify skip-link, hamburger nav toggle, and styled dialog modal exist in base layout."""
        res = staff_client.get('/dashboard')
        assert res.status_code == 200

        # Skip to main content link
        assert b"skip-link" in res.data
        assert b"Skip to main content" in res.data
        assert b'href="#main-content"' in res.data

        # Mobile menu toggle
        assert b'id="nav-toggle"' in res.data
        assert b'aria-controls="mobile-nav"' in res.data
        assert b'aria-expanded="false"' in res.data

        # Mobile nav drawer
        assert b'id="mobile-nav"' in res.data

        # Styled Confirmation & Alert Modal
        assert b'id="app-modal"' in res.data
        assert b'role="dialog"' in res.data
        assert b'aria-modal="true"' in res.data
        assert b'id="modal-confirm-btn"' in res.data
        assert b'id="modal-cancel-btn"' in res.data


# =============================================================================
# 5. Print-Friendly Report Card Page
# =============================================================================
class TestPrintFriendlyReportCard:
    """Verify dedicated print-friendly academic report card page."""

    def test_staff_view_student_report_card(self, app, staff_client):
        """Staff views print-friendly report card for student with college header and grades."""
        with app.app_context():
            student = query_db("SELECT id, name, register_number FROM students WHERE register_number = 'REG999'", one=True)
            sid = student['id']

        res = staff_client.get(f'/students/{sid}/report-card')
        assert res.status_code == 200
        assert b"INSTITUTE OF HIGHER EDUCATION &amp; TECHNOLOGY" in res.data
        assert b"OFFICIAL GRADE CARD" in res.data
        assert b"Print Report Card" in res.data
        assert b"window.print()" in res.data
        assert b"printable-report-card" in res.data
        assert b"ANNA UNIVERSITY 10-POINT GRADING SYSTEM SCALE" in res.data
        assert b"Controller of Examinations" in res.data

    def test_student_portal_report_card_view(self, student_client):
        """Student accesses their own printable report card via /student/report-card."""
        res = student_client.get('/student/report-card')
        assert res.status_code == 200
        assert b"INSTITUTE OF HIGHER EDUCATION &amp; TECHNOLOGY" in res.data
        assert b"printable-report-card" in res.data
        assert b"window.print()" in res.data

    def test_student_cannot_access_other_student_report_card(self, app, student_client):
        """Student cannot access another student's report card via staff endpoint (403 forbidden)."""
        with app.app_context():
            student = query_db("SELECT id FROM students WHERE register_number = 'REG999'", one=True)
            sid = student['id']

        res = student_client.get(f'/students/{sid}/report-card')
        assert res.status_code == 403
