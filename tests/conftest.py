"""
Pytest configuration and test fixtures for Smart Student Management System.
Provides isolated SQLite databases, test clients, authentication helpers,
and user fixtures for admin, staff, and student roles.
"""

import os
import sqlite3
import sys
import tempfile

import pytest
from werkzeug.security import generate_password_hash

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.app import create_app
from backend.config import Config
from database.db import execute_db, init_db
from database.init_db import migrate_tables


def seed_test_database(db_path: str):
    """Seed an isolated test database with admin, staff, and student accounts."""
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    migrate_tables(conn)

    # Seed Admin User
    admin_pwd = generate_password_hash("Admin@123", method="pbkdf2:sha256")
    execute_db(
        "INSERT INTO users (username, password_hash, role, full_name, email) VALUES (?, ?, ?, ?, ?)",
        ("admin", admin_pwd, "admin", "Admin User", "admin@test.edu"),
        custom_db=conn
    )

    # Seed Staff User
    staff_pwd = generate_password_hash("Staff@123", method="pbkdf2:sha256")
    execute_db(
        "INSERT INTO users (username, password_hash, role, full_name, email) VALUES (?, ?, ?, ?, ?)",
        ("staff", staff_pwd, "staff", "Staff User", "staff@test.edu"),
        custom_db=conn
    )

    # Seed Sample Student
    ins = execute_db(
        """
        INSERT INTO students (name, register_number, department, email, marks)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("Test Student", "REG999", "Computer Science", "student@test.edu", 85.0),
        custom_db=conn
    )
    student_id = ins['lastrowid']

    # Seed Attendance for Student
    execute_db(
        """
        INSERT INTO student_attendance (student_id, total_classes, attended_classes, attendance_percentage)
        VALUES (?, ?, ?, ?)
        """,
        (student_id, 60, 54, 90.0),
        custom_db=conn
    )

    # Seed Student Portal User linked to the student
    stud_pwd = generate_password_hash("Student@123", method="pbkdf2:sha256")
    execute_db(
        """
        INSERT INTO users (username, password_hash, role, full_name, email, student_id)
        VALUES (?, ?, 'student', ?, ?, ?)
        """,
        ("reg999", stud_pwd, "Test Student", "student@test.edu", student_id),
        custom_db=conn
    )

    # Seed an academic subject for CGPA calculation
    execute_db(
        """
        INSERT INTO student_subjects (student_id, semester, subject_code, subject_name, credits, marks, grade_point, letter_grade)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (student_id, 1, "CS101", "Introduction to Python", 3, 90.0, 10.0, "O"),
        custom_db=conn
    )

    conn.commit()
    conn.close()


@pytest.fixture
def app():
    """Create a clean Flask application instance with an isolated database for testing."""
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    upload_dir = tempfile.mkdtemp()

    class TestConfig(Config):
        TESTING = True
        SECRET_KEY = 'test-secret-key-32-chars-minimum-length-for-prod'
        DATABASE_PATH = db_path
        UPLOAD_FOLDER = upload_dir
        STORAGE_BACKEND = 'local'
        WTF_CSRF_ENABLED = False
        LOG_FILE = os.path.join(upload_dir, 'test.log')

    app_instance = create_app(TestConfig)

    with app_instance.app_context():
        seed_test_database(db_path)

    yield app_instance

    # Cleanup resources
    try:
        os.close(db_fd)
        if os.path.exists(db_path):
            os.remove(db_path)
    except OSError:
        pass


@pytest.fixture
def client(app):
    """Test client for HTTP requests."""
    return app.test_client()


def login(client, username, password):
    """Helper function to log in a user with username and password."""
    return client.post('/login', data={'username': username, 'password': password}, follow_redirects=True)


def logout(client):
    """Helper function to log out the current session."""
    return client.post('/logout', follow_redirects=True)


@pytest.fixture
def admin_client(client):
    """Client authenticated as administrator."""
    login(client, 'admin', 'Admin@123')
    return client


@pytest.fixture
def staff_client(client):
    """Client authenticated as academic staff."""
    login(client, 'staff', 'Staff@123')
    return client


@pytest.fixture
def student_client(client):
    """Client authenticated as a student."""
    login(client, 'reg999', 'Student@123')
    return client
