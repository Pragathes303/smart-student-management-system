# Database Backup & Disaster Recovery Guide

This document defines the enterprise disaster recovery architecture, automated backup scheduling, 7-day retention rotation policy, and step-by-step restoration procedures for the **Smart Student Management System**.

---

## 1. Architecture Overview

- **Storage Engine**: SQLite 3 with Write-Ahead Logging (`PRAGMA journal_mode = WAL;`) and Foreign Key enforcement (`PRAGMA foreign_keys = ON;`).
- **Backup Mechanism**: Uses SQLite's native `sqlite3_backup` online streaming API via `database/backup_db.py`. This captures a consistent point-in-time snapshot without database locking or service disruption.
- **Integrity Guarantee**: Every generated backup executes `PRAGMA integrity_check;` before being validated.
- **Retention Policy**: 7-day rolling window (`retention_days=7`). Backups older than 7 days are automatically purged during the daily backup cycle.
- **Safety Rollback**: The restore procedure creates a `.pre_restore_<timestamp>.bak` copy of the active database before replacing it.

---

## 2. CLI Command Reference

The backup and recovery CLI is located at [database/backup_db.py](file:///c:/cloud%20project/database/backup_db.py).

### Create an Immediate Snapshot Backup
```bash
python database/backup_db.py --backup
```
*Creates a timestamped snapshot in `backups/students_backup_YYYYMMDD_HHMMSS.db` and purges any snapshots older than 7 days.*

### List All Available Backups
```bash
python database/backup_db.py --list
```

### Verify Integrity of a Database File
```bash
python database/backup_db.py --verify database/students.db
```

### Restore Database from a Specific Backup
```bash
python database/backup_db.py --restore backups/students_backup_20261026_150000.db
```

---

## 3. Automated Daily Backup Scheduling

### Option A: Windows Task Scheduler (PowerShell / `schtasks`)

To run the backup daily at 02:00 AM local time:

```powershell
schtasks /create /tn "SmartStudentSystem_DailyBackup" `
    /tr "python \"C:\cloud project\database\backup_db.py\" --backup" `
    /sc daily /st 02:00 /ru SYSTEM
```

To verify the scheduled task:
```powershell
schtasks /query /tn "SmartStudentSystem_DailyBackup" /fo LIST /v
```

### Option B: Linux / POSIX Cron Job

On a Linux production server (e.g. Ubuntu / Debian / AWS EC2):

1. Open system crontab:
   ```bash
   crontab -e
   ```
2. Add the daily schedule at 02:00 AM:
   ```cron
   0 2 * * * /usr/bin/python3 /var/www/smart_student_system/database/backup_db.py --backup >> /var/log/db_backup.log 2>&1
   ```

---

## 4. Step-by-Step Disaster Recovery Procedure

Follow these steps in case of hardware failure, database corruption, or accidental data truncation:

### Step 1: Stop Application Traffic
Stop the web server or application process to prevent incoming write transactions:
```bash
# Windows
Stop-Process -Name python -Force

# Linux / systemd
sudo systemctl stop student-management
```

### Step 2: Identify the Target Restoration Snapshot
Run the list command to identify the latest healthy backup:
```bash
python database/backup_db.py --list
```
Example output:
```text
Available Database Backups in C:\cloud project\backups (3 total):
 - students_backup_20261026_150000.db       128.50 KB  (Created: 2026-10-26 15:00:00)
 - students_backup_20261025_020000.db       126.10 KB  (Created: 2026-10-25 02:00:00)
 - students_backup_20261024_020000.db       124.80 KB  (Created: 2026-10-24 02:00:00)
```

### Step 3: Verify Snapshot Integrity
Confirm the snapshot is uncorrupted before restoring:
```bash
python database/backup_db.py --verify backups/students_backup_20261026_150000.db
```
Ensure output states:
```text
Database Integrity for 'backups/students_backup_20261026_150000.db': OK
```

### Step 4: Execute Database Restore
Apply the restoration:
```bash
python database/backup_db.py --restore backups/students_backup_20261026_150000.db
```
The script will:
1. Verify the integrity of the backup file.
2. Create a timestamped `.pre_restore.bak` copy of the active database.
3. Stream the snapshot into `database/students.db` via the online backup API.
4. Verify post-restore integrity on the target database.

### Step 5: Validate Application Health & Restart Service
Run test suite or health check probe:
```bash
python -m flask --app run.py db current
pytest tests/ -k "test_phase2"
```

Restart the service:
```bash
# Windows
python run.py

# Linux / systemd
sudo systemctl start student-management
```

Confirm service health:
```bash
curl -f http://127.0.0.1:5000/health
```
Result:
```json
{"service": "Smart Student Management System", "status": "healthy", "storage": "local"}
```
