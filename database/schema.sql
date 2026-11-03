-- =============================================================================
-- Smart Student Management System - Database Schema
-- SQLite3 with Foreign Keys & Constraint Enforcement
-- Supports 3-Tier RBAC (Admin, Staff, Student), Multi-Subject GPA & Attendance,
-- Public Verification Tokens, Session Versions, and API Token Security
-- =============================================================================

PRAGMA foreign_keys = ON;

-- Users table for authentication and role-based access control (Admin, Staff, Student)
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'staff', 'student')),
    full_name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    student_id INTEGER DEFAULT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    session_version INTEGER NOT NULL DEFAULT 1,
    department TEXT DEFAULT NULL,
    assigned_subjects TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
);


-- Students table storing core academic and personal records
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    register_number TEXT UNIQUE NOT NULL,
    department TEXT NOT NULL,
    year INTEGER NOT NULL DEFAULT 1 CHECK(year >= 1 AND year <= 4),
    email TEXT NOT NULL,
    marks REAL NOT NULL CHECK(marks >= 0.0 AND marks <= 100.0),
    photo_filename TEXT DEFAULT NULL,
    phone TEXT DEFAULT NULL,
    address TEXT DEFAULT NULL,
    dob TEXT DEFAULT NULL,
    cgpa REAL DEFAULT NULL,
    verify_token TEXT UNIQUE DEFAULT NULL,
    deleted_at TIMESTAMP DEFAULT NULL,
    created_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
);

-- Semester-wise Subjects & Grading Table for GPA / CGPA Calculation
CREATE TABLE IF NOT EXISTS student_subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    semester INTEGER NOT NULL CHECK(semester >= 1 AND semester <= 8),
    subject_code TEXT NOT NULL,
    subject_name TEXT NOT NULL,
    credits INTEGER NOT NULL CHECK(credits >= 1 AND credits <= 6),
    marks REAL NOT NULL CHECK(marks >= 0.0 AND marks <= 100.0),
    grade_point REAL NOT NULL CHECK(grade_point >= 0.0 AND grade_point <= 10.0),
    letter_grade TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
);

-- Attendance Tracking Table with Shortage Alert Metrics (<75%)
CREATE TABLE IF NOT EXISTS student_attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER UNIQUE NOT NULL,
    total_classes INTEGER NOT NULL CHECK(total_classes >= 0),
    attended_classes INTEGER NOT NULL CHECK(attended_classes >= 0 AND attended_classes <= total_classes),
    attendance_percentage REAL NOT NULL CHECK(attendance_percentage >= 0.0 AND attendance_percentage <= 100.0),
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
);

-- API Tokens for programmatic REST API authorization (Bearer tokens)
CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'staff')),
    created_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    revoked INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
);

-- Audit log table for enterprise security, tracking who, what, when, old->new values, and IP
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT,
    old_value TEXT DEFAULT NULL,
    new_value TEXT DEFAULT NULL,
    ip_address TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

-- Marks Edit History Table tracking all mark and grade modifications
CREATE TABLE IF NOT EXISTS marks_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    subject_id INTEGER DEFAULT NULL,
    subject_code TEXT DEFAULT 'OVERALL',
    subject_name TEXT DEFAULT 'Overall Score',
    old_marks REAL NOT NULL,
    new_marks REAL NOT NULL,
    old_grade TEXT,
    new_grade TEXT,
    changed_by INTEGER,
    changed_by_name TEXT NOT NULL,
    reason TEXT,
    ip_address TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY (changed_by) REFERENCES users(id) ON DELETE SET NULL
);

-- Departments table for academic departments whitelist (B5)
CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- System Settings table (Feature 7: Semester result publishing lock)
CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Daily Period-wise Attendance Records (Feature 4)
CREATE TABLE IF NOT EXISTS attendance_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    date DATE NOT NULL,
    period INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL CHECK(status IN ('present', 'absent', 'late', 'excused')),
    subject_code TEXT DEFAULT 'GENERAL',
    marked_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(student_id, date, period),
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY (marked_by) REFERENCES users(id) ON DELETE SET NULL
);


-- Deterministic Schema Migrations Tracking (Phase 3)
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indices to accelerate common search and filter queries
CREATE INDEX IF NOT EXISTS idx_students_reg_no ON students(register_number);
CREATE INDEX IF NOT EXISTS idx_students_department ON students(department);
CREATE INDEX IF NOT EXISTS idx_students_year ON students(year);
CREATE INDEX IF NOT EXISTS idx_students_name ON students(name);
CREATE UNIQUE INDEX IF NOT EXISTS idx_students_verify_token ON students(verify_token);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_subjects_student ON student_subjects(student_id, semester);
CREATE INDEX IF NOT EXISTS idx_attendance_student ON student_attendance(student_id);
CREATE INDEX IF NOT EXISTS idx_api_tokens_hash ON api_tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_departments_name ON departments(name);
CREATE INDEX IF NOT EXISTS idx_attendance_records_lookup ON attendance_records(student_id, date);
CREATE INDEX IF NOT EXISTS idx_marks_history_student ON marks_history(student_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_audit_logs_user ON audit_logs(username);
CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs(timestamp);


