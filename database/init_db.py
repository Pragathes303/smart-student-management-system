"""
Database Initialization, Migration and Seeding Module
Ensures schema idempotency, backward compatibility migrations for new security columns,
and environment-aware administrator bootstrapping.
"""

import os
import sys

from werkzeug.security import generate_password_hash

# Ensure project root is in Python path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.security import generate_secure_token
from backend.utils import calculate_gpa, calculate_subject_grade_point
from database.db import _is_pg_connection, execute_db, get_db, init_db, is_postgres, query_db


def migrate_tables(conn):
    """
    Safely migrate existing schema to support security columns:
    - users: student_id, is_active, must_change_password, session_version
    - students: cgpa, verify_token, deleted_at
    - api_tokens table
    Backfills verify_token for existing students without one.
    Idempotent across both SQLite and PostgreSQL.
    """
    if is_postgres() or _is_pg_connection(conn):
        # On PostgreSQL, all tables, columns, and indexes are created by schema.sql in init_db().
        # Idempotently seed default institutional departments and settings if not present.
        dept_map = [
            ("CSE", "Computer Science"),
            ("IT", "Information Technology"),
            ("ECE", "Electronics & Comm."),
            ("EEE", "Electrical Engineering"),
            ("MECH", "Mechanical Engineering"),
            ("CIVIL", "Civil Engineering"),
            ("DS", "Data Science"),
            ("AI", "Artificial Intelligence"),
        ]
        for code, name in dept_map:
            execute_db(
                "INSERT INTO departments (code, name) VALUES (?, ?) ON CONFLICT (code) DO NOTHING",
                (code, name),
                commit=True,
                custom_db=conn,
            )

        execute_db(
            "INSERT INTO system_settings (key, value) VALUES ('semester_results_published', '1') ON CONFLICT (key) DO NOTHING",
            commit=True,
            custom_db=conn,
        )

        migrations = [
            (1, '001_initial_schema'),
            (2, '002_security_hardening'),
            (3, '003_departments_whitelist'),
            (4, '004_attendance_settings_selfservice'),
            (5, '005_hash_plaintext_passwords'),
            (6, '006_phase2_integrity_constraints'),
            (7, '007_phase3_staff_assignments_attendance_subjects'),
        ]
        for v, n in migrations:
            execute_db(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?) ON CONFLICT (version) DO NOTHING",
                (v, n),
                commit=True,
                custom_db=conn,
            )
        return
    # 1. Update users table columns
    user_cols = [r['name'] for r in query_db("PRAGMA table_info(users)", custom_db=conn)]
    
    if 'student_id' not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN student_id INTEGER DEFAULT NULL;")
    if 'is_active' not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;")
    if 'must_change_password' not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0;")
    if 'session_version' not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 1;")
    conn.commit()

    # 2. Update students table columns
    student_cols = [r['name'] for r in query_db("PRAGMA table_info(students)", custom_db=conn)]
    
    if 'cgpa' not in student_cols:
        conn.execute("ALTER TABLE students ADD COLUMN cgpa REAL DEFAULT NULL;")
    if 'verify_token' not in student_cols:
        conn.execute("ALTER TABLE students ADD COLUMN verify_token TEXT DEFAULT NULL;")
    if 'deleted_at' not in student_cols:
        conn.execute("ALTER TABLE students ADD COLUMN deleted_at TIMESTAMP DEFAULT NULL;")
    if 'year' not in student_cols:
        conn.execute("ALTER TABLE students ADD COLUMN year INTEGER NOT NULL DEFAULT 1;")
    if 'phone' not in student_cols:
        conn.execute("ALTER TABLE students ADD COLUMN phone TEXT DEFAULT NULL;")
    if 'address' not in student_cols:
        conn.execute("ALTER TABLE students ADD COLUMN address TEXT DEFAULT NULL;")
    if 'dob' not in student_cols:
        conn.execute("ALTER TABLE students ADD COLUMN dob TEXT DEFAULT NULL;")
    conn.commit()

    # 2b. Update audit_logs table columns
    audit_cols = [r['name'] for r in query_db("PRAGMA table_info(audit_logs)", custom_db=conn)]
    if 'old_value' not in audit_cols:
        conn.execute("ALTER TABLE audit_logs ADD COLUMN old_value TEXT DEFAULT NULL;")
    if 'new_value' not in audit_cols:
        conn.execute("ALTER TABLE audit_logs ADD COLUMN new_value TEXT DEFAULT NULL;")
    conn.commit()

    # 2c. Ensure marks_history table exists
    conn.execute("""
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
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_marks_history_student ON marks_history(student_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_students_reg_no ON students(register_number);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_students_department ON students(department);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_students_year ON students(year);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_user ON audit_logs(username);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs(timestamp);")
    conn.commit()

    # 3. Backfill any students lacking a public verify_token
    unverified_students = query_db("SELECT id FROM students WHERE verify_token IS NULL", custom_db=conn)
    for s in unverified_students:
        tok = generate_secure_token(24)
        conn.execute("UPDATE students SET verify_token = ? WHERE id = ?", (tok, s['id']))
    if unverified_students:
        conn.commit()

    # 4. Ensure api_tokens table exists
    conn.execute("""
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
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_api_tokens_hash ON api_tokens(token_hash);")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_students_verify_token ON students(verify_token);")

    # 5. Ensure departments and schema_migrations tables exist (B5 & Phase 3)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS departments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_departments_name ON departments(name);")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Seed predefined institutional departments if empty (B5)
    dept_map = [
        ("CSE", "Computer Science"),
        ("IT", "Information Technology"),
        ("ECE", "Electronics & Comm."),
        ("EEE", "Electrical Engineering"),
        ("MECH", "Mechanical Engineering"),
        ("CIVIL", "Civil Engineering"),
        ("DS", "Data Science"),
        ("AI", "Artificial Intelligence")
    ]
    for code, name in dept_map:
        conn.execute("INSERT OR IGNORE INTO departments (code, name) VALUES (?, ?)", (code, name))

    # 6. Ensure system_settings and attendance_records exist (Phase 5)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('semester_results_published', '1');")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS attendance_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            date DATE NOT NULL,
            period INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL CHECK(status IN ('present', 'absent', 'late', 'excused')),
            marked_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(student_id, date, period),
            FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
            FOREIGN KEY (marked_by) REFERENCES users(id) ON DELETE SET NULL
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_attendance_records_lookup ON attendance_records(student_id, date);")

    # 6b. Update users and attendance_records for Phase 3 staff assignments and subjects
    user_cols = [r['name'] for r in query_db("PRAGMA table_info(users)", custom_db=conn)]
    if 'department' not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN department TEXT DEFAULT NULL;")
    if 'assigned_subjects' not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN assigned_subjects TEXT DEFAULT NULL;")

    att_cols = [r['name'] for r in query_db("PRAGMA table_info(attendance_records)", custom_db=conn)]
    if 'subject_code' not in att_cols:
        conn.execute("ALTER TABLE attendance_records ADD COLUMN subject_code TEXT DEFAULT 'GENERAL';")
    conn.commit()

    # 7. One-time migration for legacy plain-text passwords (Phase 1)
    migrate_plaintext_passwords(conn)

    # Record migration milestones
    conn.execute("INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (1, '001_initial_schema');")
    conn.execute("INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (2, '002_security_hardening');")
    conn.execute("INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (3, '003_departments_whitelist');")
    conn.execute("INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (4, '004_attendance_settings_selfservice');")
    conn.execute("INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (5, '005_hash_plaintext_passwords');")
    conn.execute("INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (6, '006_phase2_integrity_constraints');")
    conn.execute("INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (7, '007_phase3_staff_assignments_attendance_subjects');")
    conn.commit()



def is_password_hash(val: str | None) -> bool:
    """Determine if a stored string is already an accepted cryptographic password hash."""
    if not val:
        return False
    val = str(val).strip()
    if val.startswith(('pbkdf2:sha256:', 'pbkdf2:sha1:', 'scrypt:', '$2b$', '$2a$', '$2y$', '$argon2')):
        return True
    if '$' in val and any(m in val.split('$', 1)[0].lower() for m in ('pbkdf2', 'scrypt', 'sha256', 'bcrypt')):
        return True
    return False


def migrate_plaintext_passwords(conn=None) -> int:
    """
    One-time migration to hash all legacy plain-text passwords in the users table
    using werkzeug.security generate_password_hash (PBKDF2:SHA-256).
    Returns the number of user accounts migrated.
    """
    if conn is None:
        conn = get_db()

    users = query_db("SELECT id, username, password_hash FROM users", custom_db=conn)
    migrated_count = 0
    for u in users:
        raw_pwd = u['password_hash']
        if not is_password_hash(raw_pwd):
            new_hash = generate_password_hash(raw_pwd, method="pbkdf2:sha256")
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (new_hash, u['id'])
            )
            migrated_count += 1

    if migrated_count > 0:
        conn.commit()
        print(f"[+] Migrated {migrated_count} plain-text password(s) to secure PBKDF2:SHA-256 hashes.")

    return migrated_count


def seed_database(db_path=None):
    """
    Initialize database schema, apply migrations, and seed environment-aware accounts.
    - Production: Bootstraps admin user strictly from ADMIN_USERNAME / ADMIN_PASSWORD.
    - Development: Seeds initial demo dataset only if the database is currently empty.
    """
    init_db(db_path)
    conn = get_db(db_path)
    migrate_tables(conn)

    env = os.getenv('FLASK_ENV', 'development').lower()

    if env == 'production':
        # Production bootstrap
        admin_count = query_db("SELECT COUNT(*) as cnt FROM users WHERE role = 'admin'", one=True, custom_db=conn)
        if not admin_count or admin_count['cnt'] == 0:
            admin_user = os.getenv('ADMIN_USERNAME')
            admin_pwd = os.getenv('ADMIN_PASSWORD')
            admin_email = os.getenv('ADMIN_EMAIL', 'admin@institution.edu')

            if not admin_user or not admin_pwd:
                raise RuntimeError(
                    "Production deployment requires ADMIN_USERNAME and ADMIN_PASSWORD environment variables to be set."
                )

            pwd_hash = generate_password_hash(admin_pwd, method="pbkdf2:sha256")
            execute_db(
                """
                INSERT INTO users (username, password_hash, role, full_name, email, is_active, must_change_password, session_version)
                VALUES (?, ?, 'admin', 'System Administrator', ?, 1, 0, 1)
                """,
                (admin_user.strip().lower(), pwd_hash, admin_email.strip().lower()),
                commit=True,
                custom_db=conn
            )
            print(f"[+] Initial production administrator '{admin_user}' bootstrapped successfully.")
        conn.close()
        return

    # Development mode: Seed demo records only if users table is empty
    existing_users = query_db("SELECT COUNT(*) as cnt FROM users", one=True, custom_db=conn)
    if existing_users and existing_users['cnt'] > 0:
        conn.close()
        return

    print("=" * 60)
    print("Seeding initial administrator and demo records from environment...")
    print("=" * 60)

    # Initial Admin provisioned from environment (S6)
    from backend.security import validate_password_strength

    admin_user = os.getenv('ADMIN_USERNAME', 'admin').strip().lower()
    admin_pwd = os.getenv('ADMIN_PASSWORD')
    admin_email = os.getenv('ADMIN_EMAIL', 'admin@institution.edu').strip().lower()

    if not admin_pwd:
        import secrets
        admin_pwd = secrets.token_urlsafe(16) + "@1Aa"

    is_valid, msg = validate_password_strength(admin_pwd)
    if not is_valid:
        raise ValueError(f"ADMIN_PASSWORD does not meet security requirements: {msg}")

    staff_user = os.getenv('STAFF_USERNAME', 'staff').strip().lower()
    staff_pwd = os.getenv('STAFF_PASSWORD')
    staff_email = os.getenv('STAFF_EMAIL', 'staff@institution.edu').strip().lower()
    if not staff_pwd:
        import secrets
        staff_pwd = secrets.token_urlsafe(16) + "@1Bb"

    initial_users = [
        (admin_user, admin_pwd, "admin", "System Administrator", admin_email),
        (staff_user, staff_pwd, "staff", "Academic Staff Member", staff_email)
    ]

    for uname, pwd, role, fname, email in initial_users:
        pwd_hash = generate_password_hash(pwd, method="pbkdf2:sha256")
        execute_db(
            """
            INSERT INTO users (username, password_hash, role, full_name, email, is_active, must_change_password, session_version)
            VALUES (?, ?, ?, ?, ?, 1, 1, 1)
            """,
            (uname, pwd_hash, role, fname, email),
            commit=True,
            custom_db=conn
        )

    # Sample Students
    sample_students = [
        ("Aarav Sharma", "REG2024001", "Computer Science", "aarav.sharma@example.edu", 92.5),
        ("Ananya Iyer", "REG2024002", "Information Technology", "ananya.iyer@example.edu", 88.0),
        ("Rohan Verma", "REG2024003", "Electronics & Comm.", "rohan.verma@example.edu", 74.5),
        ("Priya Patel", "REG2024004", "Data Science", "priya.patel@example.edu", 95.0),
        ("Vikram Singh", "REG2024005", "Mechanical Engineering", "vikram.singh@example.edu", 63.0),
        ("Sneha Reddy", "REG2024006", "Computer Science", "sneha.reddy@example.edu", 81.0),
        ("Karthik Menon", "REG2024007", "Electronics & Comm.", "karthik.menon@example.edu", 42.0),
        ("Diya Mukherjee", "REG2024008", "Data Science", "diya.mukherjee@example.edu", 89.5),
    ]

    admin_row = query_db("SELECT id FROM users WHERE username = 'admin'", one=True, custom_db=conn)
    admin_id = admin_row["id"] if admin_row else 1

    for name, reg_no, dept, email, marks in sample_students:
        vtok = generate_secure_token(24)
        ins = execute_db(
            """
            INSERT INTO students (name, register_number, department, email, marks, created_by, verify_token)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, reg_no, dept, email, marks, admin_id, vtok),
            commit=True,
            custom_db=conn
        )
        student_id = ins['lastrowid']

        # Portal account
        uname = reg_no.lower()
        pwd_hash = generate_password_hash("Student@123", method="pbkdf2:sha256")
        execute_db(
            """
            INSERT INTO users (username, password_hash, role, full_name, email, student_id, is_active, must_change_password, session_version)
            VALUES (?, ?, 'student', ?, ?, ?, 1, 0, 1)
            """,
            (uname, pwd_hash, name, email, student_id),
            commit=True,
            custom_db=conn
        )

        # Attendance
        total_c, att_c = (60, 43) if reg_no == 'REG2024005' else ((60, 35) if reg_no == 'REG2024007' else (60, 52 + (student_id % 7)))
        pct = round((att_c / total_c) * 100.0, 2)
        execute_db(
            """
            INSERT INTO student_attendance (student_id, total_classes, attended_classes, attendance_percentage)
            VALUES (?, ?, ?, ?)
            """,
            (student_id, total_c, att_c, pct),
            commit=True,
            custom_db=conn
        )

        # Subjects
        sample_subs = [
            (1, "CS101", "Problem Solving & Python", 3, 90.0 if marks >= 80 else marks),
            (1, "MA101", "Matrices & Calculus", 4, 85.0 if marks >= 80 else max(35.0, marks - 5)),
            (2, "CS201", "Data Structures & Algos", 4, 88.0 if marks >= 80 else marks)
        ]
        sub_rows = []
        for sem, code, sname, cred, smark in sample_subs:
            gp, ltr = calculate_subject_grade_point(smark)
            execute_db(
                """
                INSERT INTO student_subjects (student_id, semester, subject_code, subject_name, credits, marks, grade_point, letter_grade)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (student_id, sem, code, sname, cred, smark, gp, ltr),
                commit=True,
                custom_db=conn
            )
            sub_rows.append({'credits': cred, 'grade_point': gp})

        cgpa_calc = calculate_gpa(sub_rows)
        execute_db("UPDATE students SET cgpa = ? WHERE id = ?", (cgpa_calc['gpa'], student_id), commit=True, custom_db=conn)

    conn.close()
    print("Database initialization and development seeding completed.")


if __name__ == '__main__':
    seed_database()
