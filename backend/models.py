"""
SQLAlchemy Models and Database Schema for Flask-Migrate (Alembic)
Defines all domain entities, constraints, foreign keys, and indexes for:
- Users (RBAC: Admin, Staff, Student)
- Students (Academic & personal details with year, reg_no, marks constraints)
- StudentSubjects (Semester-wise coursework and grade points)
- StudentAttendance (Aggregate metrics and shortage monitoring)
- AttendanceRecords (Period-wise daily logs)
- AuditLogs (Full audit trail with old_value -> new_value tracking)
- MarksHistory (Audit trail for every mark/grade modification)
- Departments (Institutional whitelist)
- SystemSettings (Feature toggles & locks)
- ApiTokens (Programmatic REST API bearer tokens)
"""

from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

db = SQLAlchemy()


class User(db.Model):
    """User account model for Admin, Staff, and Student authentication."""
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default='staff')
    full_name = Column(String(120), nullable=False)
    email = Column(String(120), unique=True, nullable=False)
    student_id = Column(Integer, ForeignKey('students.id', ondelete='CASCADE'), nullable=True)
    is_active = Column(Integer, nullable=False, default=1)
    must_change_password = Column(Integer, nullable=False, default=0)
    session_version = Column(Integer, nullable=False, default=1)
    department = Column(String(80), nullable=True)
    assigned_subjects = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


    __table_args__ = (
        CheckConstraint("role IN ('admin', 'staff', 'student')", name='chk_users_role'),
    )


class Student(db.Model):
    """Core student model storing personal and aggregate academic information."""
    __tablename__ = 'students'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False, index=True)
    register_number = Column(String(50), unique=True, nullable=False, index=True)
    department = Column(String(80), nullable=False, index=True)
    year = Column(Integer, nullable=False, default=1, index=True)
    email = Column(String(120), nullable=False)
    marks = Column(Float, nullable=False)
    photo_filename = Column(String(255), nullable=True)
    phone = Column(String(30), nullable=True)
    address = Column(Text, nullable=True)
    dob = Column(String(20), nullable=True)
    cgpa = Column(Float, nullable=True)
    verify_token = Column(String(64), unique=True, nullable=True, index=True)
    deleted_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("marks >= 0.0 AND marks <= 100.0", name='chk_students_marks_range'),
        CheckConstraint("year >= 1 AND year <= 4", name='chk_students_year_range'),
        Index('idx_students_reg_no', 'register_number'),
        Index('idx_students_department', 'department'),
        Index('idx_students_year', 'year'),
    )


class StudentSubject(db.Model):
    """Semester-wise academic subjects and grades model."""
    __tablename__ = 'student_subjects'

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey('students.id', ondelete='CASCADE'), nullable=False, index=True)
    semester = Column(Integer, nullable=False)
    subject_code = Column(String(30), nullable=False)
    subject_name = Column(String(150), nullable=False)
    credits = Column(Integer, nullable=False)
    marks = Column(Float, nullable=False)
    grade_point = Column(Float, nullable=False)
    letter_grade = Column(String(5), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("semester >= 1 AND semester <= 8", name='chk_subject_semester_range'),
        CheckConstraint("credits >= 1 AND credits <= 6", name='chk_subject_credits_range'),
        CheckConstraint("marks >= 0.0 AND marks <= 100.0", name='chk_subject_marks_range'),
        CheckConstraint("grade_point >= 0.0 AND grade_point <= 10.0", name='chk_subject_grade_point_range'),
        Index('idx_subjects_student', 'student_id', 'semester'),
    )


class StudentAttendance(db.Model):
    """Aggregate attendance percentage and class counter model."""
    __tablename__ = 'student_attendance'

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey('students.id', ondelete='CASCADE'), unique=True, nullable=False, index=True)
    total_classes = Column(Integer, nullable=False, default=60)
    attended_classes = Column(Integer, nullable=False, default=52)
    attendance_percentage = Column(Float, nullable=False)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("total_classes >= 0", name='chk_attendance_total_positive'),
        CheckConstraint("attended_classes >= 0 AND attended_classes <= total_classes", name='chk_attendance_valid_range'),
        CheckConstraint("attendance_percentage >= 0.0 AND attendance_percentage <= 100.0", name='chk_attendance_percentage_range'),
    )


class AttendanceRecord(db.Model):
    """Period-wise daily attendance records model."""
    __tablename__ = 'attendance_records'

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey('students.id', ondelete='CASCADE'), nullable=False, index=True)
    date = Column(Date, nullable=False)
    period = Column(Integer, nullable=False, default=1)
    status = Column(String(20), nullable=False)
    subject_code = Column(String(30), nullable=True, default='GENERAL')
    marked_by = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


    __table_args__ = (
        CheckConstraint("status IN ('present', 'absent', 'late', 'excused')", name='chk_attendance_record_status'),
        UniqueConstraint('student_id', 'date', 'period', name='uq_student_date_period'),
        Index('idx_attendance_records_lookup', 'student_id', 'date'),
    )


class AuditLog(db.Model):
    """Enterprise security audit log table capturing who, what, when, old->new values, and IP."""
    __tablename__ = 'audit_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    username = Column(String(80), nullable=False, index=True)
    action = Column(String(80), nullable=False, index=True)
    details = Column(Text, nullable=True)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)


class MarksHistory(db.Model):
    """Immutable audit trail capturing every student mark and grade edit."""
    __tablename__ = 'marks_history'

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey('students.id', ondelete='CASCADE'), nullable=False, index=True)
    subject_id = Column(Integer, nullable=True)
    subject_code = Column(String(30), nullable=False, default='OVERALL')
    subject_name = Column(String(150), nullable=False, default='Overall Score')
    old_marks = Column(Float, nullable=False)
    new_marks = Column(Float, nullable=False)
    old_grade = Column(String(10), nullable=True)
    new_grade = Column(String(10), nullable=True)
    changed_by = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    changed_by_name = Column(String(80), nullable=False)
    reason = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index('idx_marks_history_student', 'student_id'),
    )


class Department(db.Model):
    """Academic departments institutional whitelist model."""
    __tablename__ = 'departments'

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(20), unique=True, nullable=False)
    name = Column(String(100), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class SystemSetting(db.Model):
    """System-wide configuration settings and locks."""
    __tablename__ = 'system_settings'

    key = Column(String(80), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ApiToken(db.Model):
    """REST API bearer authentication token hashes."""
    __tablename__ = 'api_tokens'

    id = Column(Integer, primary_key=True, autoincrement=True)
    token_hash = Column(String(128), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    role = Column(String(20), nullable=False)
    created_by = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    revoked = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint("role IN ('admin', 'staff')", name='chk_api_tokens_role'),
    )


class SchemaMigration(db.Model):
    """Schema migrations tracker model."""
    __tablename__ = 'schema_migrations'

    version = Column(Integer, primary_key=True)
    name = Column(String(150), nullable=False)
    applied_at = Column(DateTime, default=datetime.utcnow)
