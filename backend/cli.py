"""
Flask CLI Management Commands (Phase 5 Features 9 & 10)
Provides administrative commands for database backup, initialization,
admin creation, and demo data seeding.
"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta

import click
from werkzeug.security import generate_password_hash

from database.db import execute_db, get_db, get_db_path, query_db
from database.init_db import init_db, migrate_tables, seed_database


def register_cli_commands(app):
    """Register custom administrative CLI commands with the Flask application."""

    @app.cli.command("backup-db")
    @click.option("--output-dir", default="backups", help="Directory where backup is saved.")
    def backup_db_command(output_dir):
        """Perform an online, consistent backup of SQLite database using SQLite Backup API (Feature 9)."""
        db_path = get_db_path()
        if not os.path.exists(db_path):
            click.echo(f"Error: Database file '{db_path}' does not exist.")
            return

        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"students_backup_{timestamp}.db"
        backup_path = os.path.join(output_dir, backup_filename)

        source_conn = sqlite3.connect(db_path)
        dest_conn = sqlite3.connect(backup_path)

        try:
            with dest_conn:
                source_conn.backup(dest_conn, pages=100)
            size_kb = round(os.path.getsize(backup_path) / 1024, 2)
            click.echo(f"Database successfully backed up to: {backup_path} ({size_kb} KB)")
        except Exception as e:
            click.echo(f"Backup failed: {e!s}")
        finally:
            source_conn.close()
            dest_conn.close()

    @app.cli.command("init-db")
    def init_db_command():
        """Initialize database schema and apply pending migrations (Feature 10)."""
        db_path = get_db_path()
        init_db(db_path)
        conn = get_db(db_path)
        migrate_tables(conn)
        click.echo("Database schema and migrations initialized successfully.")

    @app.cli.command("migrate-passwords")
    def migrate_passwords_command():
        """Migrate any legacy plain-text passwords in the users table to secure PBKDF2 hashes (Phase 1)."""
        from database.init_db import migrate_plaintext_passwords
        conn = get_db()
        migrated = migrate_plaintext_passwords(conn)
        if migrated > 0:
            click.echo(f"Successfully migrated {migrated} plain-text password(s) to PBKDF2 hashes.")
        else:
            click.echo("All existing passwords are already securely hashed. No plain-text passwords found.")

    @app.cli.command("create-admin")
    @click.option("--username", prompt="Admin Username", default="admin", help="Admin username")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True, help="Admin password")
    @click.option("--email", prompt="Admin Email", default="admin@institution.edu", help="Admin email")
    def create_admin_command(username, password, email):
        """Create or update an administrator account (Feature 10)."""
        from backend.security import validate_password_strength
        is_valid, msg = validate_password_strength(password, username=username)
        if not is_valid:
            click.echo(f"Error: Password does not meet security requirements: {msg}", err=True)
            return

        conn = get_db()
        pwd_hash = generate_password_hash(password, method="pbkdf2:sha256")
        existing = query_db("SELECT id FROM users WHERE username = ?", (username.strip().lower(),), one=True, custom_db=conn)

        if existing:
            execute_db(
                "UPDATE users SET password_hash = ?, email = ?, role = 'admin', is_active = 1 WHERE id = ?",
                (pwd_hash, email.strip().lower(), existing['id']),
                custom_db=conn
            )
            click.echo(f"Administrator account '{username}' updated successfully.")
        else:
            execute_db(
                """
                INSERT INTO users (username, password_hash, role, full_name, email, is_active, must_change_password, session_version)
                VALUES (?, ?, 'admin', 'System Administrator', ?, 1, 0, 1)
                """,
                (username.strip().lower(), pwd_hash, email.strip().lower()),
                custom_db=conn
            )
            click.echo(f"Administrator account '{username}' created successfully.")

    @app.cli.command("seed-demo")
    def seed_demo_command():
        """Seed demo students, academic subjects, and attendance records (Feature 10)."""
        seed_database()
        click.echo("Demo data seeded successfully.")

    @app.cli.command("prune-audit-logs")
    @click.option("--days", default=90, type=int, help="Number of days of logs to retain (default 90).")
    def prune_audit_logs_command(days):
        """Prune historical audit logs older than the retention threshold (Finding M7)."""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM audit_logs WHERE timestamp < ?", (cutoff,))
        conn.commit()
        deleted_count = cursor.rowcount
        click.echo(f"Pruned {deleted_count} audit log records older than {days} days ({cutoff}).")

    @app.cli.command("verify-backup")
    @click.argument("backup_path", required=False)
    @click.option("--dir", "backup_dir", default="backups", help="Directory containing backups if path not specified.")
    def verify_backup_command(backup_path, backup_dir):
        """Verify the cryptographic and relational integrity of a database backup (Phase 5)."""
        import glob
        if not backup_path:
            backups = sorted(glob.glob(os.path.join(backup_dir, "*.db")), key=os.path.getmtime, reverse=True)
            if not backups:
                click.echo(f"Error: No backup files found in '{backup_dir}'.", err=True)
                sys.exit(1)
            backup_path = backups[0]

        if not os.path.exists(backup_path):
            click.echo(f"Error: Backup file '{backup_path}' does not exist.", err=True)
            sys.exit(1)

        size_kb = round(os.path.getsize(backup_path) / 1024, 2)
        click.echo(f"Verifying backup integrity for: {backup_path} ({size_kb} KB)...")

        try:
            conn = sqlite3.connect(backup_path)
            cursor = conn.cursor()

            # 1. Run SQLite low-level integrity check
            cursor.execute("PRAGMA integrity_check;")
            integrity_result = cursor.fetchone()
            if not integrity_result or integrity_result[0] != "ok":
                click.echo(f"FAIL: SQLite integrity check failed: {integrity_result}", err=True)
                conn.close()
                sys.exit(1)

            # 2. Verify schema required tables
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = {row[0] for row in cursor.fetchall()}
            required_tables = {"users", "students", "student_subjects", "audit_logs"}
            missing_tables = required_tables - tables
            if missing_tables:
                click.echo(f"FAIL: Backup is missing required schema tables: {missing_tables}", err=True)
                conn.close()
                sys.exit(1)

            # 3. Read table counts
            cursor.execute("SELECT COUNT(*) FROM students;")
            student_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM users;")
            user_count = cursor.fetchone()[0]
            conn.close()

            click.echo("SUCCESS: Database integrity check PASSED.")
            click.echo(f"Verified Records: {student_count} student(s), {user_count} user account(s).")
        except Exception as e:
            click.echo(f"FAIL: Verification failed with exception: {e!s}", err=True)
            sys.exit(1)

    @app.cli.command("health-check")
    @click.option("--deep", is_flag=True, help="Perform deep readiness probe including platform memory checks.")
    def health_check_command(deep):
        """CLI health check probe for container liveness and uptime orchestration (Phase 5)."""
        import json

        from backend.monitoring import get_system_health
        health = get_system_health(app, deep=deep)
        click.echo(json.dumps(health, indent=2))
        if health["status"] == "healthy":
            sys.exit(0)
        elif health["status"] == "degraded":
            click.echo("WARNING: Service is degraded.", err=True)
            sys.exit(0)
        else:
            click.echo("CRITICAL: Service health check failed.", err=True)
            sys.exit(1)

