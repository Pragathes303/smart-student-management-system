"""
Database Backup & Disaster Recovery Utility Module
Supports online, atomic SQLite snapshot backups, 7-day retention rotation,
integrity verification, and one-step disaster recovery restores.

CLI Usage:
    python database/backup_db.py --backup
    python database/backup_db.py --restore backups/students_backup_20261026_120000.db
    python database/backup_db.py --list
    python database/backup_db.py --verify database/students.db
"""

import argparse
import glob
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta

# Project Root and Path Resolution
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.getenv('DATABASE_PATH', os.path.join(ROOT_DIR, 'database', 'students.db'))
BACKUP_DIR = os.path.join(ROOT_DIR, 'backups')
DEFAULT_RETENTION_DAYS = 7


def verify_integrity(db_path: str) -> bool:
    """Verify SQLite database integrity via PRAGMA integrity_check."""
    if not os.path.exists(db_path):
        print(f"[!] Target database does not exist: {db_path}", file=sys.stderr)
        return False
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        rows = cursor.fetchall()
        conn.close()
        if rows and rows[0][0].lower() == 'ok':
            return True
        print(f"[!] Database integrity check failed for {db_path}: {rows}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[!] Database integrity verification error for {db_path}: {e}", file=sys.stderr)
        return False


def rotate_backups(target_dir=BACKUP_DIR, retention_days=DEFAULT_RETENTION_DAYS) -> list[str]:
    """
    Purge snapshot backups older than the retention threshold (default: 7 days).
    Returns list of deleted file paths.
    """
    if not os.path.exists(target_dir):
        return []

    cutoff = datetime.now() - timedelta(days=retention_days)
    pattern = os.path.join(target_dir, "students_backup_*.db")
    deleted_files = []

    for file_path in glob.glob(pattern):
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
            if mtime < cutoff:
                os.remove(file_path)
                deleted_files.append(file_path)
                print(f"[+] Rotated expired backup ({retention_days}+ days old): {os.path.basename(file_path)}")
        except Exception as e:
            print(f"[!] Failed to remove expired backup {file_path}: {e}", file=sys.stderr)

    return deleted_files


def backup_database(source_path=DB_PATH, target_dir=BACKUP_DIR, retention_days=DEFAULT_RETENTION_DAYS) -> str | None:
    """
    Create a consistent, point-in-time snapshot backup of the database using SQLite's
    online backup API, followed by integrity verification and 7-day retention rotation.
    """
    if not os.path.exists(source_path):
        print(f"[!] Source database file not found at: {source_path}", file=sys.stderr)
        return None

    os.makedirs(target_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = os.path.join(target_dir, f"students_backup_{timestamp}.db")

    source_conn = None
    dest_conn = None
    try:
        source_conn = sqlite3.connect(source_path)
        dest_conn = sqlite3.connect(backup_file)

        # Atomic SQLite Online Backup API
        with dest_conn:
            source_conn.backup(dest_conn, pages=100)

        # Verify backup integrity
        dest_conn.close()
        dest_conn = None

        if not verify_integrity(backup_file):
            if os.path.exists(backup_file):
                os.remove(backup_file)
            raise RuntimeError("Generated backup failed SQLite integrity check.")

        size_kb = round(os.path.getsize(backup_file) / 1024, 2)
        print(f"[SUCCESS] Database successfully backed up to: {backup_file} ({size_kb} KB)")

        # Rotate backups older than retention window
        rotate_backups(target_dir=target_dir, retention_days=retention_days)

        return backup_file
    except Exception as e:
        print(f"[ERROR] Backup failed: {e}", file=sys.stderr)
        if os.path.exists(backup_file):
            try:
                os.remove(backup_file)
            except Exception:
                pass
        return None
    finally:
        if source_conn:
            source_conn.close()
        if dest_conn:
            dest_conn.close()


def restore_database(backup_file: str, target_path=DB_PATH) -> bool:
    """
    Restore the active database from a specified snapshot file.
    Creates a pre-restore safety copy, verifies backup integrity,
    and applies the restoration using SQLite's online backup API.
    """
    if not os.path.exists(backup_file):
        print(f"[!] Backup file not found: {backup_file}", file=sys.stderr)
        return False

    print(f"[*] Verifying integrity of backup file: {backup_file} ...")
    if not verify_integrity(backup_file):
        print("[!] Backup file failed integrity verification. Restore aborted.", file=sys.stderr)
        return False

    # Safety copy of current database if present
    pre_restore_bak = None
    if os.path.exists(target_path):
        pre_restore_bak = f"{target_path}.pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
        try:
            shutil.copy2(target_path, pre_restore_bak)
            print(f"[*] Pre-restore safety copy saved to: {pre_restore_bak}")
        except Exception as e:
            print(f"[!] Failed to create pre-restore safety copy: {e}", file=sys.stderr)

    backup_conn = None
    target_conn = None
    try:
        os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
        backup_conn = sqlite3.connect(backup_file)
        target_conn = sqlite3.connect(target_path)

        with target_conn:
            backup_conn.backup(target_conn, pages=100)

        target_conn.close()
        target_conn = None

        if not verify_integrity(target_path):
            raise RuntimeError("Restored database failed integrity check.")

        print(f"[SUCCESS] Database successfully restored from '{backup_file}' to '{target_path}'.")
        return True
    except Exception as e:
        print(f"[ERROR] Restore failed: {e}", file=sys.stderr)
        # Attempt recovery from pre-restore safety copy if available
        if pre_restore_bak and os.path.exists(pre_restore_bak):
            try:
                shutil.copy2(pre_restore_bak, target_path)
                print(f"[*] Restored previous database state from safety copy: {pre_restore_bak}")
            except Exception as re:
                print(f"[!] Failed to rollback to safety copy: {re}", file=sys.stderr)
        return False
    finally:
        if backup_conn:
            backup_conn.close()
        if target_conn:
            target_conn.close()


def list_backups(target_dir=BACKUP_DIR):
    """List all available snapshot backups with timestamps and sizes."""
    if not os.path.exists(target_dir):
        print(f"No backups directory found at: {target_dir}")
        return

    pattern = os.path.join(target_dir, "students_backup_*.db")
    files = sorted(glob.glob(pattern), reverse=True)
    if not files:
        print(f"No snapshot backups found in: {target_dir}")
        return

    print("=" * 70)
    print(f"Available Database Backups in {target_dir} ({len(files)} total):")
    print("=" * 70)
    for f in files:
        mtime = datetime.fromtimestamp(os.path.getmtime(f)).strftime('%Y-%m-%d %H:%M:%S')
        size_kb = round(os.path.getsize(f) / 1024, 2)
        print(f" - {os.path.basename(f):<35} {size_kb:>8} KB  (Created: {mtime})")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Smart Student Management System - Database Backup & Disaster Recovery")
    parser.add_argument('--backup', action='store_true', help="Perform an immediate database backup and 7-day retention rotation")
    parser.add_argument('--restore', type=str, metavar="BACKUP_PATH", help="Restore database from specified backup file")
    parser.add_argument('--list', action='store_true', help="List all available database snapshots")
    parser.add_argument('--verify', type=str, metavar="DB_PATH", help="Verify integrity of specified SQLite database file")
    parser.add_argument('--retention', type=int, default=DEFAULT_RETENTION_DAYS, help="Backup retention period in days (default: 7)")

    args = parser.parse_args()

    if args.verify:
        is_ok = verify_integrity(args.verify)
        print(f"Database Integrity for '{args.verify}': {'OK' if is_ok else 'CORRUPT/FAILED'}")
        sys.exit(0 if is_ok else 1)
    elif args.list:
        list_backups()
    elif args.restore:
        success = restore_database(args.restore)
        sys.exit(0 if success else 1)
    elif args.backup or len(sys.argv) == 1:
        res = backup_database(retention_days=args.retention)
        sys.exit(0 if res else 1)
    else:
        parser.print_help()
