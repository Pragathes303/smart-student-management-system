"""
Application Logging & Audit Trail Module
Configures structured file and console logging, and logs key system
actions to both log files and the SQLite audit_logs table.
"""

import json
import logging
import os
from logging.handlers import RotatingFileHandler

from flask import request


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter for cloud aggregators (CloudWatch, ELK, Datadog)."""

    def format(self, record):
        log_record = {
            "timestamp": self.formatTime(record, self.datefmt or "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_record)


def setup_logger(app):
    """Configure rotating file handler and console handler for the Flask application."""
    log_file = app.config.get('LOG_FILE', 'logs/app.log')
    log_level_name = app.config.get('LOG_LEVEL', 'INFO')
    log_level = getattr(logging, log_level_name, logging.INFO)
    log_format = str(app.config.get('LOG_FORMAT', 'standard')).lower()

    # Ensure log directory exists
    log_dir = os.path.dirname(os.path.abspath(log_file))
    os.makedirs(log_dir, exist_ok=True)

    # Root logger formatting
    if log_format == 'json':
        formatter = JSONFormatter(datefmt='%Y-%m-%dT%H:%M:%SZ')
    else:
        formatter = logging.Formatter(
            '[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

    # 1. Rotating File Handler (Max 5MB per file, 5 backup files)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)

    # 2. Console (Stream) Handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)

    # Apply to Flask app logger
    app.logger.setLevel(log_level)
    for h in app.logger.handlers:
        h.close()
    app.logger.handlers.clear()
    app.logger.addHandler(file_handler)
    app.logger.addHandler(console_handler)

    app.logger.info("Application logging initialized. Log file: %s (format: %s)", log_file, log_format)


def record_audit(action, details, user_id=None, username="system", ip_address=None, old_value=None, new_value=None):
    """
    Log an event to the application logger and insert into the database audit_logs table,
    recording who, what, when, old value -> new value, and client IP.
    
    Security: Always uses parameterized SQL execution.
    """
    from flask import current_app

    from database.db import execute_db

    if ip_address is None:
        try:
            ip_address = request.remote_addr if request else "127.0.0.1"
        except RuntimeError:
            ip_address = "127.0.0.1"

    # Serialize dict or list to JSON string for structured auditing
    old_str = json.dumps(old_value, default=str) if isinstance(old_value, (dict, list)) else (str(old_value) if old_value is not None else None)
    new_str = json.dumps(new_value, default=str) if isinstance(new_value, (dict, list)) else (str(new_value) if new_value is not None else None)

    # Log to Python logger
    if current_app:
        current_app.logger.info(
            "AUDIT [%s] User '%s' (ID: %s, IP: %s): %s | Old: %s | New: %s",
            action, username, user_id, ip_address, details, old_str, new_str
        )

    # Insert into SQLite audit table
    try:
        execute_db(
            """
            INSERT INTO audit_logs (user_id, username, action, details, old_value, new_value, ip_address)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, username, action, details, old_str, new_str, ip_address),
            commit=True
        )
    except Exception as e:
        if current_app:
            current_app.logger.error("Failed to write audit log to database: %s", str(e))


def record_marks_history(
    student_id,
    old_marks,
    new_marks,
    changed_by=None,
    changed_by_name="system",
    old_grade=None,
    new_grade=None,
    subject_id=None,
    subject_code="OVERALL",
    subject_name="Overall Score",
    reason=None,
    ip_address=None
):
    """
    Log an immutable audit trail entry whenever a student's marks or grade are modified.
    """
    from flask import current_app

    from database.db import execute_db

    if ip_address is None:
        try:
            ip_address = request.remote_addr if request else "127.0.0.1"
        except RuntimeError:
            ip_address = "127.0.0.1"

    try:
        execute_db(
            """
            INSERT INTO marks_history (
                student_id, subject_id, subject_code, subject_name,
                old_marks, new_marks, old_grade, new_grade,
                changed_by, changed_by_name, reason, ip_address
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                student_id, subject_id, subject_code, subject_name,
                float(old_marks), float(new_marks), old_grade, new_grade,
                changed_by, changed_by_name, reason, ip_address
            ),
            commit=True
        )
    except Exception as e:
        if current_app:
            current_app.logger.error("Failed to write marks history to database: %s", str(e))


def audit_alert(severity, action, details, user_id=None, username="system", ip_address=None):
    """
    Log a high-priority security alert tag (SEV1, SEV2, SEV3) and persist to audit trail.
    """
    from flask import current_app

    sev_tag = f"ALERT-{severity.upper()}"
    full_details = f"[{sev_tag}] {details}"
    if current_app:
        current_app.logger.warning("SECURITY %s [%s] User '%s': %s", sev_tag, action, username, details)
    record_audit(action=action, details=full_details, user_id=user_id, username=username, ip_address=ip_address)
