"""
Comprehensive Test Suite for Phase 5: Quality and Monitoring
Covers:
1. System Health Probes (/health and deep probe ?deep=1)
2. Health Check 503 Service Unavailable when DB is down
3. Prometheus Exposition Format (/metrics)
4. JSON Operational Metrics (/api/v1/metrics)
5. Admin Monitoring Dashboard (/admin/monitoring) and RBAC controls
6. Diagnostic Snapshot Download (/admin/monitoring/diagnostics)
7. CLI verify-backup and health-check commands
8. Structured JSON Logging (JSONFormatter) and audit_alert helper
9. High-coverage tests for storage, import service, and CLI
"""

import json
import logging
import os
import sqlite3
from unittest.mock import patch

import pytest

from backend.logger import JSONFormatter, audit_alert
from backend.monitoring import (
    check_database_health,
    check_storage_health,
    generate_diagnostic_report,
    get_cache_health,
    get_memory_usage_mb,
    get_process_uptime,
    get_prometheus_metrics,
    get_recent_security_audits,
    get_system_health,
    get_system_metrics,
)
from backend.storage import LocalStorageHandler, S3StorageHandler
from database.db import execute_db, query_db


# =============================================================================
# 1. Monitoring Service Unit Tests
# =============================================================================
class TestMonitoringService:
    """Test core functions in backend/monitoring.py."""

    def test_process_uptime(self, app):
        with app.app_context():
            uptime = get_process_uptime(app)
            assert isinstance(uptime, float)
            assert uptime >= 0.0

    def test_memory_usage_mb(self):
        rss = get_memory_usage_mb()
        assert isinstance(rss, float)
        assert rss >= 0.0

    def test_database_health_check(self, app):
        with app.app_context():
            healthy, latency, err = check_database_health()
            assert healthy is True
            assert isinstance(latency, float)
            assert latency >= 0.0
            assert err is None

    def test_storage_health_check_local(self, app):
        with app.app_context():
            healthy, backend_type, details = check_storage_health(app)
            assert healthy is True
            assert backend_type == "local"
            assert details.get("writable") is True

    def test_storage_health_check_s3(self, app):
        app.config['STORAGE_BACKEND'] = 's3'
        app.config['S3_BUCKET_NAME'] = 'test-institution-bucket'
        with app.app_context():
            healthy, backend_type, details = check_storage_health(app)
            assert healthy is True
            assert backend_type == "s3"
            assert details.get("bucket") == "test-institution-bucket"
        app.config['STORAGE_BACKEND'] = 'local'

    def test_cache_health(self, app):
        with app.app_context():
            healthy, details = get_cache_health()
            assert healthy is True
            assert "entries" in details

    def test_system_health_deep_mode(self, app):
        with app.app_context():
            health = get_system_health(app, deep=True)
            assert health["status"] in ("healthy", "degraded")
            assert "checks" in health
            assert health["checks"]["database"]["status"] == "healthy"
            assert "system" in health
            assert "python_version" in health["system"]

    def test_system_metrics_aggregation(self, app):
        with app.app_context():
            metrics = get_system_metrics(app)
            assert "students" in metrics
            assert "total" in metrics["students"]
            assert "users" in metrics
            assert "security" in metrics
            assert isinstance(metrics["students"]["total"], int)

    def test_prometheus_exposition_format(self, app):
        with app.app_context():
            text = get_prometheus_metrics(app)
            assert "# HELP app_uptime_seconds" in text
            assert "# TYPE app_uptime_seconds gauge" in text
            assert "app_uptime_seconds" in text
            assert "app_database_status 1" in text
            assert "app_students_total" in text

    def test_recent_security_audits(self, app):
        with app.app_context():
            execute_db(
                "INSERT INTO audit_logs (action, details, username) VALUES (?, ?, ?)",
                ("TEST_EVENT", "Monitoring test audit", "admin"),
                commit=True
            )
            audits = get_recent_security_audits(limit=5)
            assert len(audits) > 0
            assert any(a["action"] == "TEST_EVENT" for a in audits)

    def test_diagnostic_report_generation(self, app):
        with app.app_context():
            report = generate_diagnostic_report(app)
            assert "report_generated_at" in report
            assert "health" in report
            assert "database" in report
            assert "size_kb" in report["database"]


# =============================================================================
# 2. HTTP Health & Metrics Endpoints
# =============================================================================
class TestHealthAndMetricsEndpoints:
    """Test HTTP routes /health, /metrics, /api/v1/metrics."""

    def test_health_check_default_liveness(self, client):
        res = client.get('/health')
        assert res.status_code == 200
        data = res.get_json()
        assert data["status"] == "healthy"
        assert "uptime_seconds" in data
        assert "storage" in data

    def test_health_check_deep_readiness(self, client):
        res = client.get('/health?deep=1')
        assert res.status_code == 200
        data = res.get_json()
        assert data["status"] in ("healthy", "degraded")
        assert "checks" in data
        assert data["checks"]["database"]["status"] == "healthy"
        assert "system" in data

    def test_health_check_unhealthy_database_returns_503(self, client):
        """Verify load balancers receive 503 when the database check fails."""
        with patch('backend.monitoring.check_database_health', return_value=(False, 120.0, "Connection refused")):
            res = client.get('/health')
            assert res.status_code == 503
            data = res.get_json()
            assert data["status"] == "unhealthy"

    def test_prometheus_metrics_route(self, admin_client):
        res = admin_client.get('/metrics')
        assert res.status_code == 200
        assert "text/plain" in res.mimetype
        assert b"app_database_status" in res.data
        assert b"app_students_total" in res.data

    def test_prometheus_metrics_unauthenticated_redirects(self, client):
        res = client.get('/metrics', follow_redirects=False)
        assert res.status_code == 302

    def test_api_v1_metrics_route(self, admin_client):
        res = admin_client.get('/api/v1/metrics')
        assert res.status_code == 200
        data = res.get_json()
        assert "students" in data
        assert "users" in data
        assert "security" in data

    def test_api_v1_metrics_unauthenticated_redirects(self, client):
        res = client.get('/api/v1/metrics', follow_redirects=False)
        assert res.status_code == 302

    def test_prometheus_metrics_bearer_token_auth(self, monkeypatch, client):
        monkeypatch.setenv('METRICS_TOKEN', 'secret-prom-token')
        # Missing or invalid token
        res_invalid = client.get('/metrics', headers={'Authorization': 'Bearer wrong-token'})
        assert res_invalid.status_code == 401

        # Valid bearer token
        res_valid = client.get('/metrics', headers={'Authorization': 'Bearer secret-prom-token'})
        assert res_valid.status_code == 200
        assert b"app_database_status" in res_valid.data

    def test_api_v1_metrics_bearer_token_auth(self, monkeypatch, client):
        monkeypatch.setenv('METRICS_TOKEN', 'secret-prom-token')
        # Invalid token
        res_invalid = client.get('/api/v1/metrics', headers={'Authorization': 'Bearer wrong-token'})
        assert res_invalid.status_code == 401

        # Valid token
        res_valid = client.get('/api/v1/metrics', headers={'Authorization': 'Bearer secret-prom-token'})
        assert res_valid.status_code == 200
        assert "students" in res_valid.get_json()


# =============================================================================
# 3. Admin Monitoring Dashboard & Diagnostics
# =============================================================================
class TestAdminMonitoringDashboard:
    """Verify RBAC and display on /admin/monitoring."""

    def test_monitoring_dashboard_requires_auth(self, client):
        res = client.get('/admin/monitoring', follow_redirects=False)
        assert res.status_code in (302, 401)

    def test_monitoring_dashboard_blocks_student(self, student_client):
        res = student_client.get('/admin/monitoring')
        assert res.status_code == 403

    def test_monitoring_dashboard_blocks_staff(self, staff_client):
        res = staff_client.get('/admin/monitoring')
        assert res.status_code == 403

    def test_monitoring_dashboard_allows_admin(self, admin_client):
        res = admin_client.get('/admin/monitoring')
        assert res.status_code == 200
        assert b"System Health &amp; Operational Monitoring" in res.data or b"System Health" in res.data
        assert b"Database Subsystem" in res.data
        assert b"Storage Subsystem" in res.data
        assert b"Download Diagnostics" in res.data

    def test_admin_diagnostics_download(self, admin_client):
        res = admin_client.get('/admin/monitoring/diagnostics')
        assert res.status_code == 200
        assert res.mimetype == "application/json"
        assert "attachment" in res.headers.get("Content-Disposition", "")
        data = json.loads(res.data.decode('utf-8'))
        assert "health" in data
        assert "database" in data


# =============================================================================
# 4. CLI Commands (verify-backup, health-check, prune-audit-logs)
# =============================================================================
class TestCliCommands:
    """Verify Flask CLI commands for backup verification and health check."""

    def test_verify_backup_valid_db(self, app, tmp_path):
        runner = app.test_cli_runner()
        # Create a valid minimal database backup
        backup_file = str(tmp_path / "valid_backup.db")
        conn = sqlite3.connect(backup_file)
        c = conn.cursor()
        c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT);")
        c.execute("CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT);")
        c.execute("CREATE TABLE student_subjects (id INTEGER PRIMARY KEY, subject_code TEXT);")
        c.execute("CREATE TABLE audit_logs (id INTEGER PRIMARY KEY, action TEXT);")
        c.execute("INSERT INTO users VALUES (1, 'admin');")
        c.execute("INSERT INTO students VALUES (1, 'Test Student');")
        conn.commit()
        conn.close()

        result = runner.invoke(args=["verify-backup", backup_file])
        assert result.exit_code == 0
        assert "Database integrity check PASSED" in result.output
        assert "1 student(s)" in result.output

    def test_verify_backup_missing_file(self, app):
        runner = app.test_cli_runner()
        result = runner.invoke(args=["verify-backup", "nonexistent_backup.db"])
        assert result.exit_code != 0
        assert "does not exist" in result.output

    def test_verify_backup_corrupted_file(self, app, tmp_path):
        runner = app.test_cli_runner()
        corrupt_file = str(tmp_path / "corrupt_backup.db")
        with open(corrupt_file, "wb") as f:
            f.write(b"NOT_A_SQLITE_DATABASE_HEADER_CORRUPTED_BYTES")

        result = runner.invoke(args=["verify-backup", corrupt_file])
        assert result.exit_code != 0
        assert "FAIL" in result.output

    def test_cli_health_check_command(self, app):
        runner = app.test_cli_runner()
        result = runner.invoke(args=["health-check"])
        assert result.exit_code == 0
        assert '"status": "healthy"' in result.output

    def test_cli_health_check_deep_flag(self, app):
        runner = app.test_cli_runner()
        result = runner.invoke(args=["health-check", "--deep"])
        assert result.exit_code == 0
        assert '"python_version"' in result.output

    def test_cli_migrate_passwords(self, app):
        runner = app.test_cli_runner()
        result = runner.invoke(args=["migrate-passwords"])
        assert result.exit_code == 0
        assert "password" in result.output.lower()

    def test_cli_create_admin_and_validation(self, app):
        runner = app.test_cli_runner()
        # Invalid weak password rejected
        res_weak = runner.invoke(args=["create-admin", "--username", "testadmin", "--password", "short", "--email", "a@b.com"])
        assert "Password does not meet security requirements" in res_weak.output

        # Create new admin
        res_new = runner.invoke(args=["create-admin", "--username", "newsuper", "--password", "NewSuperAdmin@2026", "--email", "super@test.edu"])
        assert res_new.exit_code == 0
        assert "created successfully" in res_new.output

        # Update existing admin
        res_update = runner.invoke(args=["create-admin", "--username", "newsuper", "--password", "UpdatedSuperAdmin@2026", "--email", "super_up@test.edu"])
        assert res_update.exit_code == 0
        assert "updated successfully" in res_update.output

    def test_cli_prune_audit_logs(self, app):
        runner = app.test_cli_runner()
        result = runner.invoke(args=["prune-audit-logs", "--days", "365"])
        assert result.exit_code == 0
        assert "Pruned" in result.output


# =============================================================================
# 5. Structured JSON Logging & Audit Alerting
# =============================================================================
class TestStructuredLogging:
    """Verify JSONFormatter and audit_alert."""

    def test_json_formatter_output(self):
        formatter = JSONFormatter(datefmt="%Y-%m-%dT%H:%M:%SZ")
        record = logging.LogRecord(
            name="test_logger",
            level=logging.WARNING,
            pathname="test_path.py",
            lineno=42,
            msg="User login attempted with invalid credentials",
            args=(),
            exc_info=None
        )
        formatted = formatter.format(record)
        parsed = json.loads(formatted)
        assert parsed["level"] == "WARNING"
        assert parsed["logger"] == "test_logger"
        assert "invalid credentials" in parsed["message"]
        assert parsed["line"] == 42
        assert "timestamp" in parsed

    def test_audit_alert_helper(self, app):
        with app.app_context():
            audit_alert(
                severity="SEV1",
                action="ADMIN_PASSWORD_RESET",
                details="Admin reset password for staff member",
                username="admin",
                ip_address="192.168.1.50"
            )
            row = query_db(
                "SELECT * FROM audit_logs WHERE action = 'ADMIN_PASSWORD_RESET' ORDER BY id DESC",
                one=True
            )
            assert row is not None
            assert "[ALERT-SEV1]" in row["details"]
            assert row["username"] == "admin"


# =============================================================================
# 6. Additional Quality & Edge Cases (Storage & Import)
# =============================================================================
class TestQualityEdgeCases:
    """Verify edge cases for storage and utilities."""

    def test_local_storage_handler_delete_and_url(self, tmp_path):
        handler = LocalStorageHandler(upload_folder=str(tmp_path))
        test_file = tmp_path / "test_file.txt"
        test_file.write_text("sample content")

        url = handler.get_url("test_file.txt")
        assert "/static/uploads/test_file.txt" in url

        deleted = handler.delete("test_file.txt")
        assert deleted is True
        assert not test_file.exists()

        # Deleting non-existent file returns True safely
        assert handler.delete("does_not_exist.txt") is True

    def test_s3_storage_url_generation(self):
        s3 = S3StorageHandler(
            bucket_name="my-bucket",
            region="ap-south-1",
            access_key="test_key",
            secret_key="test_secret"
        )
        url = s3.get_url("student_101.jpg")
        assert "my-bucket" in url
        assert "student_101.jpg" in url
        assert "X-Amz-Signature" in url or "amazonaws.com" in url

    def test_base_storage_raises_not_implemented(self):
        from backend.storage import BaseStorageHandler
        base = BaseStorageHandler()
        assert base.is_cloud_active() is False
        with pytest.raises(NotImplementedError):
            base.save(None)
        with pytest.raises(NotImplementedError):
            base.delete("sample_id")
        with pytest.raises(NotImplementedError):
            base.get_url("sample_id")

    def test_local_storage_save_modes(self, tmp_path):
        handler = LocalStorageHandler(upload_folder=str(tmp_path))
        assert handler.save(None) is None

        class DummyFile:
            filename = "sample.jpg"
            def read(self):
                return b"fake image bytes"

        saved_name = handler.save(DummyFile())
        assert saved_name is not None
        assert os.path.exists(os.path.join(str(tmp_path), saved_name))

    def test_monitoring_degraded_and_unhealthy_branches(self, app):
        with app.app_context():
            with patch('backend.monitoring.check_storage_health', return_value=(False, "local", {"error": "read only"})):
                h = get_system_health(app)
                assert h["status"] == "degraded"

            with patch('backend.monitoring.check_database_health', return_value=(False, 0.0, "DB Down")):
                h = get_system_health(app)
                assert h["status"] == "unhealthy"
