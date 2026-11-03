# Production Monitoring, Observability & Incident Runbook

This runbook documents the monitoring architecture, health check probes, Prometheus metrics exposition, structured logging, database integrity verification, and incident response procedures for the **Smart Student Management System**.

---

## 1. Observability Architecture Overview

The application provides a multi-tiered observability stack engineered for production cloud deployments (AWS EC2, ECS, Render, or bare-metal):

| Telemetry Type | Endpoint / Interface | Format | Authentication | Primary Consumer |
| :--- | :--- | :--- | :--- | :--- |
| **Liveness Probe** | `GET /health` | JSON | Public | AWS ALB, ECS Healthcheck, Nginx, UptimeRobot |
| **Readiness Probe** | `GET /health?deep=1` | JSON | Public | Kubernetes, Deploy verification scripts |
| **Prometheus Metrics** | `GET /metrics` | Text (0.0.4) | Public | Prometheus Scraper, AWS CloudWatch Agent |
| **Operational Telemetry** | `GET /api/v1/metrics` | JSON | Session / API Token | Custom dashboards, Data analytics |
| **Monitoring Dashboard** | `GET /admin/monitoring` | HTML UI | Admin Role | Systems Administrators, Institutional IT |
| **Diagnostics Snapshot** | `GET /admin/monitoring/diagnostics` | JSON File | Admin Role | Post-mortem analysis, Support escalations |
| **CLI Health Check** | `flask health-check [--deep]` | JSON / Exit Code | Local CLI / Shell | Docker `HEALTHCHECK`, Deployment scripts |
| **Backup Integrity** | `flask verify-backup [path]` | CLI Text / Exit Code | Local CLI / Shell | Backup cron jobs, Disaster recovery audits |

---

## 2. Health Probes & Cloud Probing

### 2.1. Liveness Probe (`GET /health`)
- **Purpose**: Fast check verifying the Python WSGI worker is responsive and serving requests.
- **Latency**: Sub-millisecond (< 2ms).
- **HTTP Status Codes**:
  - `200 OK`: Application process is active and running.
  - `503 Service Unavailable`: Critical subsystem (e.g., SQLite connection) is completely unreachable.
- **Sample Response**:
  ```json
  {
    "status": "healthy",
    "service": "Smart Student Management System",
    "storage": "local",
    "uptime_seconds": 3612.4
  }
  ```

### 2.2. Deep Readiness Probe (`GET /health?deep=1`)
- **Purpose**: Performs active dependency validation before routing production traffic.
- **Subsystem Checks**:
  1. **Database**: Executes `SELECT 1 AS ping`, records round-trip latency in milliseconds.
  2. **Storage**: Verifies upload directory writeability or pings S3 bucket.
  3. **Cache**: Inspects in-memory dashboard TTL cache entry counts.
  4. **System Vitals**: Collects resident memory set (RSS in MB), PID, platform version.
- **Sample Response**:
  ```json
  {
    "status": "healthy",
    "service": "Smart Student Management System",
    "version": "2.0.0",
    "environment": "production",
    "timestamp": "2026-10-29T20:25:00Z",
    "uptime_seconds": 3612.4,
    "checks": {
      "database": {
        "status": "healthy",
        "latency_ms": 1.15,
        "engine": "sqlite",
        "error": null
      },
      "storage": {
        "status": "healthy",
        "backend": "local",
        "details": {
          "path": "/app/frontend/static/uploads",
          "writable": true
        }
      },
      "cache": {
        "status": "healthy",
        "details": {
          "entries": 4,
          "ttl_seconds": 60
        }
      }
    },
    "system": {
      "platform": "Linux-5.15.0-x86_64",
      "python_version": "3.13.5",
      "memory_rss_mb": 48.6,
      "process_id": 142
    }
  }
  ```

---

## 3. Prometheus & CloudWatch Metrics Integration

### 3.1. Prometheus Metric Definitions
The `GET /metrics` endpoint provides metrics conforming to the Prometheus text exposition standard:

```text
# Process and Latency
app_uptime_seconds                 Gauge: Process uptime in seconds
app_memory_rss_mb                  Gauge: Resident set memory usage in megabytes
app_database_ping_latency_ms       Gauge: Round-trip database ping latency (ms)
app_database_status                Gauge: 1 = Healthy, 0 = Unhealthy

# Academic Telemetry
app_students_total                 Gauge: Active registered students
app_students_by_department         Gauge: Partitioned by department label
app_students_arrears_total         Gauge: Students with marks < 50 or Grade RA
app_students_low_attendance_total  Gauge: Students with attendance < 75%

# Institutional Security
app_users_total                    Gauge: Total registered user accounts
app_users_active_total             Gauge: Active user accounts
app_audit_events_total             Counter: Total security audit records logged
```

### 3.2. Authentication & Access Control for `/metrics`
The `/metrics` endpoint is protected against unauthorized access and requires either:
1. **Prometheus Bearer Token**: Pass header `Authorization: Bearer <METRICS_TOKEN>` configured in your environment (`METRICS_TOKEN` in `.env`).
2. **Administrative Session**: Authenticated browser access by an active `admin` user.
3. **Nginx IP Allowlisting**: Optional network-level restriction in `cloud/nginx.conf` (e.g. `allow 10.0.0.0/16; deny all;`).

### 3.3. Prometheus Scrape Configuration (`prometheus.yml`)
Add the following job to your Prometheus server configuration:

```yaml
scrape_configs:
  - job_name: 'smart_student_system'
    scrape_interval: 15s
    scrape_timeout: 5s
    metrics_path: '/metrics'
    scheme: 'http'
    bearer_token: 'YOUR_METRICS_TOKEN_HERE'  # Matches METRICS_TOKEN in .env
    static_configs:
      - targets: ['127.0.0.1:5000']
    relabel_configs:
      - target_label: environment
        replacement: production
```

### 3.3. AWS CloudWatch Metrics via CloudWatch Agent
To stream metrics into Amazon CloudWatch, configure the CloudWatch Agent Prometheus receiver in `/opt/aws/amazon-cloudwatch-agent/etc/prometheus.yaml`:

```yaml
global:
  scrape_interval: 30s

scrape_configs:
  - job_name: 'student_system_ecs'
    static_configs:
      - targets: ['127.0.0.1:5000']
```

---

## 4. Structured JSON Logging

### 4.1. Configuration
In production environments, structured JSON logging can be activated in `.env`:

```ini
LOG_FILE=logs/app.log
LOG_LEVEL=INFO
LOG_FORMAT=json
```

When active, log records are formatted as single-line JSON objects ready for ingestion by AWS CloudWatch Logs, Datadog, or Grafana Loki:

```json
{
  "timestamp": "2026-10-29T20:25:12Z",
  "level": "INFO",
  "logger": "backend.app",
  "message": "AUDIT [LOGIN_SUCCESS] User 'staff' (ID: 2, IP: 192.168.1.10): User 'staff' logged in with role 'staff' | Old: None | New: None",
  "module": "logger",
  "line": 84
}
```

### 4.2. Security Alert Logging (`audit_alert`)
Critical administrative and security operations are flagged with automated severity tags:
- `[ALERT-SEV1]`: High-priority event (Admin role creation, mass database modifications, semester lock toggle).
- `[ALERT-SEV2]`: Medium-priority event (Repeated failed logins, password resets, marks override after publication).
- `[ALERT-SEV3]`: Warning event (File upload rejections, invalid CSRF attempts).

---

## 5. Database Backup & Integrity Verification Runbook

### 5.1. Performing an Online Backup
Run the online SQLite Backup API command:
```bash
# Snapshot to backups/ directory
flask backup-db

# Or custom destination
flask backup-db --output-dir /mnt/secure-backups
```

### 5.2. Verifying Backup Integrity
Before archiving or replicating backups to Amazon S3, verify SQLite page consistency and required relational tables:
```bash
flask verify-backup backups/students_backup_20261029_203000.db
```
**Output on Successful Verification**:
```text
Verifying backup integrity for: backups/students_backup_20261029_203000.db (124.5 KB)...
SUCCESS: Database integrity check PASSED.
Verified Records: 25 student(s), 4 user account(s).
```

### 5.3. Recommended Automated Backup Cron
Add the following entry to the application server crontab (`crontab -e`):

```bash
# Nightly database backup at 02:00 AM UTC with automatic integrity verification
0 2 * * * cd /opt/student-system && /opt/student-system/venv/bin/flask backup-db && /opt/student-system/venv/bin/flask verify-backup >> /opt/student-system/logs/backup.log 2>&1

# Prune audit logs older than 90 days on the 1st of every month
0 3 1 * * cd /opt/student-system && /opt/student-system/venv/bin/flask prune-audit-logs --days 90 >> /opt/student-system/logs/prune.log 2>&1
```

---

## 6. Incident Response Playbooks

### Incident Playbook A: High Database Latency or Database Lock
- **Symptoms**: `/health?deep=1` reports `latency_ms > 100ms`, or requests log `sqlite3.OperationalError: database is locked`.
- **Triage Steps**:
  1. Inspect active processes accessing `database/students.db`:
     ```bash
     fuser database/students.db
     ```
  2. Check for long-running uncommitted transactions or stalled workers:
     ```bash
     ps aux | grep gunicorn
     ```
  3. Verify write-ahead logging (WAL) mode is active:
     ```bash
     sqlite3 database/students.db "PRAGMA journal_mode;"
     # Should return "wal"
     ```
  4. Perform checkpointing if WAL file is bloated:
     ```bash
     sqlite3 database/students.db "PRAGMA wal_checkpoint(TRUNCATE);"
     ```
  5. Restart application WSGI workers gracefully:
     ```bash
     sudo systemctl reload student-system
     ```

---

### Incident Playbook B: 500 Internal Server Errors Spike
- **Symptoms**: CloudWatch or Uptime monitoring alerts on HTTP 500 status codes.
- **Triage Steps**:
  1. Check real-time error logs:
     ```bash
     tail -n 100 -f logs/app.log | grep -E "ERROR|CRITICAL"
     ```
  2. Review recent audit log changes in `/admin/monitoring` or SQLite:
     ```bash
     sqlite3 database/students.db "SELECT timestamp, action, username, details FROM audit_logs ORDER BY id DESC LIMIT 15;"
     ```
  3. Verify file permissions on `frontend/static/uploads` and `database/`:
     ```bash
     ls -la database/ frontend/static/uploads/
     ```
  4. Run offline diagnostic probe:
     ```bash
     flask health-check --deep
     ```

---

### Incident Playbook C: Brute Force or Credential Stuffing Attack
- **Symptoms**: Excessive 429 Too Many Requests responses on `/login` or `/forgot-password`.
- **Triage Steps**:
  1. Extract attacking client IP addresses from `logs/app.log`:
     ```bash
     grep "RATELIMIT" logs/app.log | awk '{print $NF}' | sort | uniq -c | sort -nr | head -n 10
     ```
  2. Block abusive IPs immediately at Nginx level (`/etc/nginx/conf.d/blocklist.conf`):
     ```nginx
     deny 203.0.113.45;
     ```
  3. Reload Nginx configuration:
     ```bash
     sudo nginx -s reload
     ```
  4. Confirm genuine users can authenticate without blockage.
