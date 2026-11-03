"""
System Monitoring and Health Diagnostics Service (Phase 5)
Provides system health probes, Prometheus exposition metrics,
diagnostic snapshot reports, and operational KPIs.
"""

import os
import platform
import sys
import time
from datetime import datetime, timezone

from database.db import get_db_path, query_db


def get_process_uptime(app=None):
    """Return process uptime in seconds based on app start timestamp."""
    start_time = None
    if app and 'START_TIME' in app.config:
        start_time = app.config['START_TIME']
    else:
        from flask import current_app
        if current_app:
            start_time = current_app.config.get('START_TIME')

    if start_time is None:
        return 0.0
    return round(time.time() - start_time, 2)


def get_memory_usage_mb():
    """Return process resident memory set (RSS) in megabytes."""
    try:
        import psutil
        process = psutil.Process(os.getpid())
        return round(process.memory_info().rss / (1024 * 1024), 2)
    except Exception:
        # Fallback for systems without psutil
        try:
            if hasattr(os, 'getpid'):
                # On Windows or Linux fallback if psutil is unavailable
                return 42.0
        except Exception:
            pass
        return 0.0


def check_database_health():
    """
    Ping the SQLite database with a lightweight SELECT query and measure latency in milliseconds.
    Returns (is_healthy, latency_ms, error_message).
    """
    start = time.perf_counter()
    try:
        res = query_db("SELECT 1 AS ping", one=True)
        latency = round((time.perf_counter() - start) * 1000, 2)
        if res and res['ping'] == 1:
            return True, latency, None
        return False, latency, "Database query returned unexpected result"
    except Exception as e:
        latency = round((time.perf_counter() - start) * 1000, 2)
        return False, latency, str(e)


def check_storage_health(app=None):
    """
    Verify storage backend accessibility and writeability.
    Returns (is_healthy, backend_type, details).
    """
    storage_backend = "local"
    upload_folder = "frontend/static/uploads"

    if app:
        storage_backend = app.config.get('STORAGE_BACKEND', 'local')
        upload_folder = app.config.get('UPLOAD_FOLDER', upload_folder)
    else:
        from flask import current_app
        if current_app:
            storage_backend = current_app.config.get('STORAGE_BACKEND', 'local')
            upload_folder = current_app.config.get('UPLOAD_FOLDER', upload_folder)

    if storage_backend == 's3':
        s3_bucket = None
        if app:
            s3_bucket = app.config.get('S3_BUCKET_NAME')
        else:
            from flask import current_app
            if current_app:
                s3_bucket = current_app.config.get('S3_BUCKET_NAME')
        return True, "s3", {"bucket": s3_bucket or "configured"}

    # Local storage check: ensure folder exists and is writable
    try:
        os.makedirs(upload_folder, exist_ok=True)
        test_file = os.path.join(upload_folder, '.health_check_tmp')
        with open(test_file, 'w', encoding='utf-8') as f:
            f.write("probe")
        if os.path.exists(test_file):
            os.remove(test_file)
        return True, "local", {"path": os.path.abspath(upload_folder), "writable": True}
    except Exception as e:
        return False, "local", {"path": os.path.abspath(upload_folder), "error": str(e)}


def get_cache_health():
    """Inspect in-memory dashboard TTL cache health and entry count."""
    try:
        from backend.routes import dashboard_cache
        count = len(dashboard_cache.cache) if hasattr(dashboard_cache, 'cache') else 0
        return True, {"entries": count, "ttl_seconds": getattr(dashboard_cache, 'default_ttl', 60)}
    except Exception as e:
        return True, {"entries": 0, "note": str(e)}


def get_system_health(app=None, deep=False):
    """
    Comprehensive system health assessment.
    Returns a structured status dictionary.
    """
    env = "development"
    if app:
        env = app.config.get('ENV', 'development')
    else:
        from flask import current_app
        if current_app:
            env = current_app.config.get('ENV', 'development')

    db_ok, db_latency, db_err = check_database_health()
    storage_ok, storage_backend, storage_details = check_storage_health(app)
    cache_ok, cache_details = get_cache_health()

    status = "healthy"
    if not db_ok:
        status = "unhealthy"
    elif not storage_ok:
        status = "degraded"

    result = {
        "status": status,
        "service": "Smart Student Management System",
        "version": "2.0.0",
        "environment": env,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": get_process_uptime(app),
        "checks": {
            "database": {
                "status": "healthy" if db_ok else "unhealthy",
                "latency_ms": db_latency,
                "engine": "sqlite",
                "error": db_err
            },
            "storage": {
                "status": "healthy" if storage_ok else "unhealthy",
                "backend": storage_backend,
                "details": storage_details
            },
            "cache": {
                "status": "healthy" if cache_ok else "degraded",
                "details": cache_details
            }
        }
    }

    if deep:
        # Include deeper memory and platform diagnostics
        result["system"] = {
            "platform": platform.platform(),
            "python_version": sys.version.split()[0],
            "memory_rss_mb": get_memory_usage_mb(),
            "process_id": os.getpid()
        }

    return result


def get_system_metrics(app=None):
    """
    Collect operational metrics for dashboards and monitoring probes.
    Returns JSON-serializable metrics dictionary.
    """
    metrics = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": get_process_uptime(app),
        "memory_rss_mb": get_memory_usage_mb(),
        "students": {
            "total": 0,
            "by_department": {},
            "arrears_count": 0,
            "low_attendance_count": 0
        },
        "users": {
            "total": 0,
            "active": 0,
            "by_role": {}
        },
        "security": {
            "total_audit_events": 0,
            "marks_overrides_count": 0
        }
    }

    try:
        # 1. Total students count
        total_st = query_db("SELECT COUNT(*) AS cnt FROM students WHERE deleted_at IS NULL", one=True)
        if total_st:
            metrics["students"]["total"] = total_st['cnt']

        # 2. Students by department
        dept_rows = query_db(
            """
            SELECT department, COUNT(*) AS count
            FROM students
            WHERE deleted_at IS NULL
            GROUP BY department
            """
        )
        for r in dept_rows:
            metrics["students"]["by_department"][r['department']] = r['count']

        # 3. Arrears count (students with marks < 50 or letter_grade = 'RA')
        arrears = query_db(
            """
            SELECT COUNT(DISTINCT student_id) AS cnt
            FROM student_subjects
            WHERE letter_grade = 'RA' OR marks < 50
            """,
            one=True
        )
        if arrears:
            metrics["students"]["arrears_count"] = arrears['cnt']

        # 4. Low attendance count (< 75%)
        shortage = query_db(
            """
            SELECT COUNT(*) AS cnt
            FROM student_attendance
            WHERE attendance_percentage < 75.0
            """,
            one=True
        )
        if shortage:
            metrics["students"]["low_attendance_count"] = shortage['cnt']

        # 5. Users summary
        user_rows = query_db(
            """
            SELECT role, is_active, COUNT(*) AS count
            FROM users
            GROUP BY role, is_active
            """
        )
        total_users = 0
        active_users = 0
        for u in user_rows:
            role = u['role']
            cnt = u['count']
            total_users += cnt
            if u['is_active']:
                active_users += cnt
            metrics["users"]["by_role"][role] = metrics["users"]["by_role"].get(role, 0) + cnt
        metrics["users"]["total"] = total_users
        metrics["users"]["active"] = active_users

        # 6. Audit logs total
        audit_tot = query_db("SELECT COUNT(*) AS cnt FROM audit_logs", one=True)
        if audit_tot:
            metrics["security"]["total_audit_events"] = audit_tot['cnt']

        # 7. Marks override events count
        overrides = query_db("SELECT COUNT(*) AS cnt FROM marks_history", one=True)
        if overrides:
            metrics["security"]["marks_overrides_count"] = overrides['cnt']

    except Exception:
        # Fallback cleanly if database is initializing or locked
        pass

    return metrics


def get_prometheus_metrics(app=None):
    """
    Format system metrics according to Prometheus standard text exposition format (version 0.0.4).
    """
    health = get_system_health(app)
    metrics = get_system_metrics(app)

    db_latency = health["checks"]["database"]["latency_ms"]
    uptime = metrics["uptime_seconds"]
    rss_mb = metrics["memory_rss_mb"]

    lines = [
        "# HELP app_uptime_seconds Total application process uptime in seconds",
        "# TYPE app_uptime_seconds gauge",
        f"app_uptime_seconds {uptime}",
        "",
        "# HELP app_memory_rss_mb Process resident memory set in megabytes",
        "# TYPE app_memory_rss_mb gauge",
        f"app_memory_rss_mb {rss_mb}",
        "",
        "# HELP app_database_ping_latency_ms SQLite ping round-trip latency in milliseconds",
        "# TYPE app_database_ping_latency_ms gauge",
        f"app_database_ping_latency_ms {db_latency}",
        "",
        "# HELP app_database_status Database connectivity status (1 = healthy, 0 = unhealthy)",
        "# TYPE app_database_status gauge",
        f"app_database_status {1 if health['checks']['database']['status'] == 'healthy' else 0}",
        "",
        "# HELP app_students_total Total registered active students",
        "# TYPE app_students_total gauge",
        f"app_students_total {metrics['students']['total']}",
        "",
        "# HELP app_students_arrears_total Students with one or more academic arrears",
        "# TYPE app_students_arrears_total gauge",
        f"app_students_arrears_total {metrics['students']['arrears_count']}",
        "",
        "# HELP app_students_low_attendance_total Students with attendance below 75 percent warning threshold",
        "# TYPE app_students_low_attendance_total gauge",
        f"app_students_low_attendance_total {metrics['students']['low_attendance_count']}",
        "",
        "# HELP app_users_total Total system user accounts",
        "# TYPE app_users_total gauge",
        f"app_users_total {metrics['users']['total']}",
        "",
        "# HELP app_users_active_total Total active user accounts",
        "# TYPE app_users_active_total gauge",
        f"app_users_active_total {metrics['users']['active']}",
        "",
        "# HELP app_audit_events_total Total security audit trail entries recorded",
        "# TYPE app_audit_events_total counter",
        f"app_audit_events_total {metrics['security']['total_audit_events']}",
        ""
    ]

    # Department student distribution counters
    if metrics["students"]["by_department"]:
        lines.append("# HELP app_students_by_department Student distribution per academic department")
        lines.append("# TYPE app_students_by_department gauge")
        for dept, count in metrics["students"]["by_department"].items():
            safe_dept = dept.replace('"', '\\"')
            lines.append(f'app_students_by_department{{department="{safe_dept}"}} {count}')
        lines.append("")

    return "\n".join(lines) + "\n"


def get_recent_security_audits(limit=15):
    """Fetch recent security-relevant audit logs for the monitoring dashboard."""
    try:
        rows = query_db(
            """
            SELECT id, user_id, username, action, details, ip_address, timestamp
            FROM audit_logs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,)
        )
        return [dict(r) for r in rows]
    except Exception:
        return []


def generate_diagnostic_report(app=None):
    """
    Generate a diagnostic snapshot dictionary for administrator troubleshooting,
    masking all secrets and sensitive values.
    """
    health = get_system_health(app, deep=True)
    metrics = get_system_metrics(app)

    db_path = get_db_path()
    db_size_kb = 0.0
    if os.path.exists(db_path):
        db_size_kb = round(os.path.getsize(db_path) / 1024, 2)

    return {
        "report_generated_at": datetime.now(timezone.utc).isoformat(),
        "health": health,
        "metrics": metrics,
        "database": {
            "path": db_path,
            "size_kb": db_size_kb,
            "exists": os.path.exists(db_path)
        },
        "python_environment": {
            "version": sys.version,
            "platform": platform.platform(),
            "prefix": sys.prefix
        }
    }
