"""
Core Application Routes
Implements Student CRUD, Analytics Dashboard, Search & Filter,
CSV Export, User Management, and Cloud Storage Integrations.
"""

import csv
import hmac
import io
import json
import os
from datetime import datetime, timezone
from typing import Any

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from backend.auth import (
    admin_required,
    current_student_id,
    get_current_user,
    login_required,
    staff_required,
)
from backend.cache import dashboard_cache
from backend.grading import (
    calculate_semester_and_overall_gpa,
    calculate_sgpa,
    get_grade_and_points,
)
from backend.logger import record_audit
from backend.mailer import (
    send_marks_published_email,
    send_student_marksheet_email,
    send_welcome_student_email,
)
from backend.pdf_generator import generate_student_marksheet_pdf
from backend.security import (
    generate_secure_token,
    generate_temp_password,
    is_safe_redirect,
    sanitize_csv_cell,
    validate_and_sanitize_image,
    validate_password_strength,
)
from backend.services.import_service import ImportService
from backend.services.student_service import StudentService
from backend.storage import get_storage
from backend.utils import (
    calculate_gpa,
    calculate_grade,
    get_attendance_status,
    get_valid_departments,
    map_errors_to_fields,
    validate_date_of_birth,
    validate_phone_number,
    validate_student_data,
    validate_user_data,
)
from database.db import execute_db, get_db, query_db

routes_bp = Blueprint('routes', __name__)


def is_semester_published(semester: int | None = None) -> bool:
    """Check if semester results are published/locked for staff changes."""
    if semester is not None:
        sem_res = query_db(
            "SELECT value FROM system_settings WHERE key = ? OR key = ?",
            (f"semester_{semester}_published", f"semester_{semester}_locked"),
            one=True
        )
        if sem_res:
            return str(sem_res['value']).strip() == '1'
        return False
    # If no semester specified, check if marks_locked is explicitly set
    glob_lock = query_db("SELECT value FROM system_settings WHERE key = 'marks_locked'", one=True)
    return bool(glob_lock and str(glob_lock['value']).strip() == '1')


def check_staff_marks_permission(user: dict | None, student: dict | None, subject_code: str | None = None, semester: int | None = None) -> tuple[bool, str | None]:
    """
    Verify if user is authorized to edit academic marks/subjects.
    Rules:
    - Admin: Can always edit marks (override on locked semester is permitted and logged).
    - Staff:
        - Cannot modify marks once semester results are published/locked.
        - If staff has assigned department, student must belong to that department.
        - If staff has assigned subjects, subject_code must be in that list (or '*').
    - Others: 403 Forbidden.
    """
    if not user:
        return False, "Authentication required."

    role = user.get('role')
    if role == 'admin':
        return True, None

    if role != 'staff':
        return False, "Access Denied: Only academic staff and administrators may modify marks."

    # 1. Publish/Lock check
    if is_semester_published(semester):
        return False, "Marks for this semester have been published and locked. Only administrators can modify marks after publication."

    # 2. Department assignment check
    user_dept = (user.get('department') or '').strip()
    if user_dept and student:
        student_dept = (student.get('department') or '').strip()
        if student_dept and user_dept.lower() != student_dept.lower():
            return False, f"Access Denied: You are assigned to '{user_dept}' and cannot modify marks for '{student_dept}'."

    # 3. Subject assignment check
    user_subjects = (user.get('assigned_subjects') or '').strip()
    if user_subjects and subject_code:
        allowed = [s.strip().upper() for s in user_subjects.split(',') if s.strip()]
        if allowed and '*' not in allowed and subject_code.strip().upper() not in allowed:
            return False, f"Access Denied: You are not assigned to course '{subject_code}'. Assigned courses: {', '.join(allowed)}."

    return True, None


# -----------------------------------------------------------------------------
# System Health & Monitoring Endpoints (Phase 5 Quality & Monitoring)
# -----------------------------------------------------------------------------
@routes_bp.route('/health', methods=['GET'])
def health_check():
    """
    Service health check endpoint for cloud monitoring and uptime probes.
    Supports lightweight liveness probe (default) and deep readiness probe (?deep=1).
    """
    from backend.monitoring import get_system_health

    is_deep = request.args.get('deep', '0').strip().lower() in ('1', 'true', 'yes')
    health = get_system_health(current_app, deep=is_deep)

    http_status = 200
    if health["status"] == "unhealthy":
        http_status = 503

    if not is_deep:
        # Standard lightweight response for cloud load-balancers & container probes
        return jsonify({
            "status": health["status"],
            "service": health["service"],
            "storage": health["checks"]["storage"]["backend"],
            "uptime_seconds": health["uptime_seconds"]
        }), http_status

    return jsonify(health), http_status


@routes_bp.route('/metrics', methods=['GET'])
@login_required
def metrics():
    """
    Expose Prometheus standard exposition text metrics.
    Protected: Accessible via active Admin session or METRICS_TOKEN Bearer header.
    """
    from backend.monitoring import get_prometheus_metrics

    auth_header = request.headers.get('Authorization', '').strip()
    metrics_token = os.getenv('METRICS_TOKEN') or current_app.config.get('METRICS_TOKEN')

    # 1. Bearer Token Authentication (Prometheus scraper / external monitors)
    if auth_header.startswith('Bearer '):
        token = auth_header[7:].strip()
        if metrics_token and hmac.compare_digest(token, metrics_token):
            content = get_prometheus_metrics(current_app)
            return Response(content, mimetype="text/plain; version=0.0.4; charset=utf-8")
        return Response("Unauthorized: Invalid or missing METRICS_TOKEN\n", status=401, mimetype="text/plain")

    # 2. Admin Session Authentication (browser access)
    user = get_current_user()
    if not user:
        return redirect(url_for('auth.login', next=request.path))
    if user.get('role') != 'admin':
        abort(403)

    content = get_prometheus_metrics(current_app)
    return Response(content, mimetype="text/plain; version=0.0.4; charset=utf-8")


@routes_bp.route('/api/v1/metrics', methods=['GET'])
@login_required
def api_metrics():
    """
    Expose JSON formatted operational metrics.
    Protected: Accessible via active Admin session or METRICS_TOKEN Bearer header.
    """
    from backend.monitoring import get_system_metrics

    auth_header = request.headers.get('Authorization', '').strip()
    metrics_token = os.getenv('METRICS_TOKEN') or current_app.config.get('METRICS_TOKEN')

    # 1. Bearer Token Authentication
    if auth_header.startswith('Bearer '):
        token = auth_header[7:].strip()
        if metrics_token and hmac.compare_digest(token, metrics_token):
            return jsonify(get_system_metrics(current_app)), 200
        return jsonify({'error': 'Unauthorized', 'message': 'Invalid or missing METRICS_TOKEN'}), 401

    # 2. Admin Session Authentication
    user = get_current_user()
    if not user:
        return redirect(url_for('auth.login', next=request.path))
    if user.get('role') != 'admin':
        abort(403)

    return jsonify(get_system_metrics(current_app)), 200


@routes_bp.route('/admin/monitoring', methods=['GET'])
@login_required
@admin_required
def admin_monitoring():
    """Administrative System Health & Monitoring Dashboard."""
    from backend.monitoring import (
        get_recent_security_audits,
        get_system_health,
        get_system_metrics,
    )

    health = get_system_health(current_app, deep=True)
    metrics_data = get_system_metrics(current_app)
    recent_audits = get_recent_security_audits(limit=10)

    return render_template(
        'monitoring.html',
        health=health,
        metrics=metrics_data,
        recent_audits=recent_audits
    )


@routes_bp.route('/admin/monitoring/diagnostics', methods=['GET'])
@login_required
@admin_required
def admin_monitoring_diagnostics():
    """Download full diagnostic snapshot JSON report for system administrators."""
    from backend.monitoring import generate_diagnostic_report

    report = generate_diagnostic_report(current_app)
    filename = f"system_diagnostic_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    return Response(
        json.dumps(report, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# -----------------------------------------------------------------------------
# Privacy Policy & Data Minimization Notice (Phase 1 Security / Privacy)
# -----------------------------------------------------------------------------
@routes_bp.route('/privacy', methods=['GET'])
def privacy_policy():
    """Display the institutional Student Privacy Note and Data Minimization Policy."""
    return render_template('privacy.html')


# -----------------------------------------------------------------------------
# Dashboard & Analytics
# -----------------------------------------------------------------------------
def get_dashboard_metrics(force_refresh: bool = False) -> dict[str, Any]:
    """
    Retrieve academic analytics dashboard metrics.
    Uses in-memory TTL caching (default 180s / 3 min, configurable via DASHBOARD_CACHE_TTL).
    """
    if not force_refresh:
        cached = dashboard_cache.get('dashboard_metrics')
        if cached is not None:
            return cached

    # 1. Overall Key Performance Indicators (KPIs)
    kpis = query_db(
        """
        SELECT
            COUNT(*) AS total_students,
            ROUND(AVG(marks), 2) AS avg_marks,
            MAX(marks) AS max_marks,
            MIN(marks) AS min_marks,
            COUNT(CASE WHEN marks >= 50.0 THEN 1 END) AS pass_count,
            COUNT(CASE WHEN marks < 50.0 THEN 1 END) AS fail_count
        FROM students
        WHERE deleted_at IS NULL
        """,
        one=True
    ) or {}

    total_students = kpis.get('total_students') or 0
    avg_marks = kpis.get('avg_marks') or 0.0
    max_marks = kpis.get('max_marks') or 0.0
    pass_count = kpis.get('pass_count') or 0
    fail_count = kpis.get('fail_count') or 0

    pass_percentage = round((pass_count / total_students * 100), 1) if total_students > 0 else 0.0

    # 2. Department-wise Breakdown with Pass Percentage
    dept_stats = query_db(
        """
        SELECT
            department,
            COUNT(*) AS student_count,
            ROUND(AVG(marks), 1) AS avg_marks,
            MAX(marks) AS max_marks,
            COUNT(CASE WHEN marks >= 50.0 THEN 1 END) AS pass_count
        FROM students
        WHERE deleted_at IS NULL
        GROUP BY department
        ORDER BY student_count DESC, department ASC
        """
    )

    for dept in dept_stats:
        dept['percentage'] = round((dept['student_count'] / total_students * 100), 1) if total_students > 0 else 0
        dept['pass_percentage'] = round((dept['pass_count'] / dept['student_count'] * 100), 1) if dept['student_count'] > 0 else 0.0

    # 3. Low Attendance Shortage Watchlist (< 75%)
    low_attendance_students = query_db(
        """
        SELECT s.id, s.name, s.register_number, s.department,
               sa.attended_classes, sa.total_classes, sa.attendance_percentage
        FROM students s
        JOIN student_attendance sa ON s.id = sa.student_id
        WHERE s.deleted_at IS NULL AND sa.attendance_percentage < 75.0
        ORDER BY sa.attendance_percentage ASC, s.name ASC
        LIMIT 10
        """
    )

    # 4. Top Performing Students
    top_performers = query_db(
        """
        SELECT id, name, register_number, department, marks
        FROM students
        WHERE deleted_at IS NULL
        ORDER BY marks DESC
        LIMIT 5
        """
    )
    for s in top_performers:
        s['grade_info'] = calculate_grade(s['marks'])

    # 5. Recent Student Registrations
    recent_students = query_db(
        """
        SELECT id, name, register_number, department, marks, created_at
        FROM students
        WHERE deleted_at IS NULL
        ORDER BY created_at DESC
        LIMIT 5
        """
    )
    for s in recent_students:
        s['grade_info'] = calculate_grade(s['marks'])

    # 6. Grade Distribution for Visual Analytics (Anna University 10-point scale)
    grade_counts = query_db(
        """
        SELECT
            COUNT(CASE WHEN marks >= 90.0 THEN 1 END) AS grade_o,
            COUNT(CASE WHEN marks >= 80.0 AND marks < 90.0 THEN 1 END) AS grade_aplus,
            COUNT(CASE WHEN marks >= 70.0 AND marks < 80.0 THEN 1 END) AS grade_a,
            COUNT(CASE WHEN marks >= 60.0 AND marks < 70.0 THEN 1 END) AS grade_bplus,
            COUNT(CASE WHEN marks >= 50.0 AND marks < 60.0 THEN 1 END) AS grade_b,
            COUNT(CASE WHEN marks < 50.0 THEN 1 END) AS grade_ra
        FROM students
        WHERE deleted_at IS NULL
        """,
        one=True
    ) or {}

    chart_dept_labels = [d['department'] for d in dept_stats]
    chart_dept_data = [d['student_count'] for d in dept_stats]
    chart_grade_labels = ['O (90-100)', 'A+ (80-89)', 'A (70-79)', 'B+ (60-69)', 'B (50-59)', 'RA (<50)']
    chart_grade_data = [
        grade_counts.get('grade_o', 0),
        grade_counts.get('grade_aplus', 0),
        grade_counts.get('grade_a', 0),
        grade_counts.get('grade_bplus', 0),
        grade_counts.get('grade_b', 0),
        grade_counts.get('grade_ra', 0)
    ]

    cached = {
        'total_students': total_students,
        'avg_marks': avg_marks,
        'max_marks': max_marks,
        'pass_count': pass_count,
        'fail_count': fail_count,
        'pass_percentage': pass_percentage,
        'dept_stats': dept_stats,
        'low_attendance_students': low_attendance_students,
        'top_performers': top_performers,
        'recent_students': recent_students,
        'chart_dept_labels': chart_dept_labels,
        'chart_dept_data': chart_dept_data,
        'chart_grade_labels': chart_grade_labels,
        'chart_grade_data': chart_grade_data
    }
    ttl = current_app.config.get('DASHBOARD_CACHE_TTL', 180)
    dashboard_cache.set('dashboard_metrics', cached, ttl_seconds=ttl)
    return cached


@routes_bp.route('/')
@routes_bp.route('/dashboard')
@login_required
def dashboard():
    """
    Main Analytics Dashboard.
    Calculates total students, department counts, averages, and pass rates.
    Cached for 1-5 minutes (default 180s) with atomic invalidation.
    """
    if session.get('role') == 'student':
        return redirect(url_for('routes.student_portal'))

    cached = get_dashboard_metrics()
    storage = get_storage()

    return render_template(
        'dashboard.html',
        total_students=cached['total_students'],
        avg_marks=cached['avg_marks'],
        max_marks=cached['max_marks'],
        pass_count=cached['pass_count'],
        fail_count=cached['fail_count'],
        pass_percentage=cached['pass_percentage'],
        dept_stats=cached['dept_stats'],
        low_attendance_students=cached.get('low_attendance_students', []),
        top_performers=cached['top_performers'],
        recent_students=cached['recent_students'],
        chart_dept_labels=cached['chart_dept_labels'],
        chart_dept_data=cached['chart_dept_data'],
        chart_grade_labels=cached['chart_grade_labels'],
        chart_grade_data=cached['chart_grade_data'],
        storage_is_cloud=storage.is_cloud_active()
    )


@routes_bp.route('/api/v1/dashboard/kpis', methods=['GET'])
@login_required
@staff_required
def dashboard_live_kpis():
    """
    Live dashboard KPI endpoint for 30-second interval client polling (Phase 6).
    Returns real-time or cached academic statistics as JSON.
    """
    cached = get_dashboard_metrics()
    return jsonify({
        'success': True,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'kpis': {
            'total_students': cached['total_students'],
            'avg_marks': cached['avg_marks'],
            'max_marks': cached['max_marks'],
            'pass_percentage': cached['pass_percentage'],
            'pass_count': cached['pass_count'],
            'fail_count': cached['fail_count'],
        },
        'chart_dept_labels': cached.get('chart_dept_labels', []),
        'chart_dept_data': cached.get('chart_dept_data', []),
        'chart_grade_labels': cached.get('chart_grade_labels', []),
        'chart_grade_data': cached.get('chart_grade_data', [])
    }), 200


# -----------------------------------------------------------------------------
# Student Records: List, Search & Pagination
# -----------------------------------------------------------------------------
@routes_bp.route('/students')
@login_required
@staff_required
def list_students():
    """
    Search, filter, paginate, and view student records.
    Uses parameterized SQL queries for security.
    """
    if session.get('role') == 'student':
        return redirect(url_for('routes.student_portal'))

    search_query = (request.args.get('q') or '').strip()
    dept_filter = (request.args.get('dept') or '').strip()
    year_filter = request.args.get('year', type=int)
    grade_filter = (request.args.get('grade') or '').strip()
    status_filter = (request.args.get('status') or '').strip()
    sort_by = (request.args.get('sort') or 'name_asc').strip()

    page = request.args.get('page', 1, type=int)
    page = max(page, 1)
    per_page = 8

    # Base Filter Criteria (exclude soft-deleted records)
    where_clauses = ["s.deleted_at IS NULL"]
    params = []

    # Text Search Filter
    if search_query:
        where_clauses.append("(s.name LIKE ? OR s.register_number LIKE ? OR s.email LIKE ?)")
        wildcard = f"%{search_query}%"
        params.extend([wildcard, wildcard, wildcard])

    # Department Filter
    if dept_filter:
        where_clauses.append("s.department = ?")
        params.append(dept_filter)

    # Academic Year Filter
    if year_filter:
        where_clauses.append("s.year = ?")
        params.append(year_filter)

    # Grade & Pass/Fail Status Filter (Anna University Scale)
    if grade_filter == 'O':
        where_clauses.append("s.marks >= 90.0")
    elif grade_filter == 'A+':
        where_clauses.append("s.marks >= 80.0 AND s.marks < 90.0")
    elif grade_filter == 'A':
        where_clauses.append("s.marks >= 70.0 AND s.marks < 80.0")
    elif grade_filter == 'B+':
        where_clauses.append("s.marks >= 60.0 AND s.marks < 70.0")
    elif grade_filter == 'B':
        where_clauses.append("s.marks >= 50.0 AND s.marks < 60.0")
    elif grade_filter == 'RA' or grade_filter == 'fail' or status_filter == 'fail':
        where_clauses.append("s.marks < 50.0")
    elif grade_filter == 'pass' or status_filter == 'pass':
        where_clauses.append("s.marks >= 50.0")
    elif grade_filter == 'distinction':
        where_clauses.append("s.marks >= 80.0")
    elif status_filter == 'pass':
        where_clauses.append("s.marks >= 50.0")
    elif status_filter == 'fail':
        where_clauses.append("s.marks < 50.0")

    where_sql = " AND ".join(where_clauses)

    # 1. Total Count Query (for pagination)
    count_sql = f"SELECT COUNT(*) as cnt FROM students s WHERE {where_sql}"  # nosec B608
    count_row = query_db(count_sql, list(params), one=True)
    total_count = count_row['cnt'] if count_row else 0

    total_pages = max(1, (total_count + per_page - 1) // per_page)
    if page > total_pages and total_count > 0:
        page = total_pages

    # 2. Sorting
    sort_mapping = {
        'name_asc': 's.name ASC',
        'name_desc': 's.name DESC',
        'marks_desc': 's.marks DESC',
        'marks_asc': 's.marks ASC',
        'reg_asc': 's.register_number ASC',
        'date_desc': 's.created_at DESC'
    }
    order_clause = sort_mapping.get(sort_by, 's.name ASC')

    # 3. Paged Data Query (Single JOIN eliminating N+1 queries - B7)
    offset = (page - 1) * per_page
    paged_params = list(params) + [per_page, offset]
    data_sql = f"""
        SELECT s.id, s.name, s.register_number, s.department, s.year, s.email, s.marks, s.cgpa,
               s.photo_filename, s.created_at,
               sa.attendance_percentage
        FROM students s
        LEFT JOIN student_attendance sa ON s.id = sa.student_id
        WHERE {where_sql}
        ORDER BY {order_clause}
        LIMIT ? OFFSET ?
    """  # nosec B608

    students = query_db(data_sql, paged_params)

    # Compute grades and photo URLs for each student in-memory (0 DB queries in loop)
    storage = get_storage()
    for s in students:
        s['grade_info'] = calculate_grade(s['marks'])
        s['photo_url'] = storage.get_url(s['photo_filename']) if s['photo_filename'] else None
        att_pct = s.get('attendance_percentage')
        if att_pct is not None:
            s['attendance_pct'] = att_pct
            s['attendance_info'] = get_attendance_status(att_pct)
        else:
            s['attendance_pct'] = None
            s['attendance_info'] = None

    # Retrieve institutional departments for filter dropdown (B5)
    departments = get_valid_departments()

    return render_template(
        'students.html',
        students=students,
        search_query=search_query,
        selected_dept=dept_filter,
        selected_year=year_filter,
        selected_grade=grade_filter,
        selected_status=status_filter,
        selected_sort=sort_by,
        departments=departments,
        total_count=total_count,
        current_page=page,
        total_pages=total_pages,
        has_prev=page > 1,
        has_next=page < total_pages,
        prev_page=page - 1,
        next_page=page + 1
    )


# -----------------------------------------------------------------------------
# Student Records: Add (Create)
# -----------------------------------------------------------------------------
@routes_bp.route('/students/add', methods=['GET', 'POST'])
@login_required
@staff_required
def add_student():
    """Add a new student record with form validation and photo storage."""
    departments = get_valid_departments()

    if request.method == 'POST':
        name = request.form.get('name')
        register_number = request.form.get('register_number')
        department = request.form.get('department')
        year = request.form.get('year')
        email = request.form.get('email')
        marks = request.form.get('marks')
        phone = request.form.get('phone')
        dob = request.form.get('dob')
        photo_file = request.files.get('photo')

        is_valid, errors, cleaned = validate_student_data(
            name, register_number, department, email, marks, phone=phone, dob=dob, year=year
        )

        photo_filename = None
        if photo_file and photo_file.filename:
            is_valid_img, img_err, sanitized_buf = validate_and_sanitize_image(photo_file)
            if not is_valid_img:
                errors.append(img_err or "Invalid image file.")
            else:
                try:
                    storage = get_storage()
                    sanitized_buf.filename = photo_file.filename
                    photo_filename = storage.save(sanitized_buf)
                except Exception as e:
                    errors.append(f"Photo upload failed: {e!s}")

        if not is_valid or errors:
            for err in errors:
                flash(err, "danger")
            field_errors = map_errors_to_fields(errors)
            return render_template(
                'add_student.html',
                form_data=request.form,
                departments=departments,
                field_errors=field_errors
            )

        user_id = session.get('user_id')
        try:
            new_student_id, student_record, temp_pwd = StudentService.create_student(
                cleaned=cleaned,
                photo_filename=photo_filename,
                created_by_user_id=user_id,
                default_total_classes=current_app.config.get('DEFAULT_TOTAL_CLASSES', 60),
                default_attended_classes=current_app.config.get('DEFAULT_ATTENDED_CLASSES', 52)
            )
        except Exception as e:
            flash(f"Failed to create student: A student with this register number or email may already exist ({e}).", "danger")
            return render_template('add_student.html', form_data=request.form, departments=departments)

        stud_username = cleaned['register_number'].lower()
        send_welcome_student_email(cleaned['email'], cleaned['name'], cleaned['register_number'], temp_password=temp_pwd)

        record_audit(
            action="STUDENT_CREATED",
            details=f"Created student record '{cleaned['name']}' ({cleaned['register_number']}) in {cleaned['department']} (Year {cleaned.get('year', 1)})",
            user_id=user_id,
            username=session.get('username'),
            old_value=None,
            new_value=cleaned
        )

        flash(
            f"Student record for '{cleaned['name']}' successfully added! "
            f"Portal Username: {stud_username} | Temporary Password: {temp_pwd} (show once to student)",
            "success"
        )
        return redirect(url_for('routes.list_students'))

    return render_template('add_student.html', departments=departments, form_data={})


# -----------------------------------------------------------------------------
# Student Records: View Details
# -----------------------------------------------------------------------------
@routes_bp.route('/students/<int:student_id>')
@login_required
def view_student(student_id):
    """View detailed profile of an individual student with subjects and attendance."""
    # S1: Students may only access their own record
    if session.get('role') == 'student':
        sid = current_student_id()
        if sid != student_id:
            return render_template(
                'error.html',
                error_code=403,
                error_message="Access Denied: You may only view your own student record."
            ), 403

    student = query_db(
        """
        SELECT s.*, u.full_name as creator_name, u.username as creator_username
        FROM students s
        LEFT JOIN users u ON s.created_by = u.id
        WHERE s.id = ? AND s.deleted_at IS NULL
        """,
        (student_id,),
        one=True
    )

    if not student:
        flash("Student record not found.", "warning")
        return redirect(url_for('routes.list_students'))

    storage = get_storage()
    student_dict = dict(student)
    student_dict['grade_info'] = calculate_grade(student['marks'])
    student_dict['photo_url'] = storage.get_url(student['photo_filename']) if student['photo_filename'] else None

    # Fetch attendance
    attendance = query_db("SELECT * FROM student_attendance WHERE student_id = ?", (student_id,), one=True)
    att_info = get_attendance_status(attendance['attendance_percentage']) if attendance else None

    # Fetch semester subjects
    subjects = query_db(
        "SELECT * FROM student_subjects WHERE student_id = ? ORDER BY semester ASC, subject_code ASC",
        (student_id,)
    )
    gpa_stats = calculate_gpa(subjects)
    semester_gpa_data = calculate_semester_and_overall_gpa(subjects)

    # Check for linked portal user account (for admin password reset)
    portal_user = query_db(
        "SELECT id, username, is_active, must_change_password FROM users WHERE student_id = ?",
        (student_id,),
        one=True
    )

    # Fetch marks audit history (Phase 2)
    marks_history = query_db(
        "SELECT * FROM marks_history WHERE student_id = ? ORDER BY created_at DESC",
        (student_id,)
    )

    return render_template(
        'student_detail.html',
        student=student_dict,
        attendance=attendance,
        att_info=att_info,
        subjects=subjects,
        gpa_stats=gpa_stats,
        semester_gpa_data=semester_gpa_data,
        portal_user=portal_user,
        marks_history=marks_history
    )


# -----------------------------------------------------------------------------
# Student Records: Update (Edit)
# -----------------------------------------------------------------------------
@routes_bp.route('/students/<int:student_id>/edit', methods=['GET', 'POST'])
@login_required
@staff_required
def edit_student(student_id):
    """Update an existing student record with optimistic concurrency conflict detection."""
    student = query_db("SELECT * FROM students WHERE id = ? AND deleted_at IS NULL", (student_id,), one=True)
    if not student:
        flash("Student record not found.", "warning")
        return redirect(url_for('routes.list_students'))

    departments = get_valid_departments()
    storage = get_storage()

    if request.method == 'POST':
        # Optimistic concurrency check (Phase 2): detect concurrent modifications
        original_updated_at = request.form.get('original_updated_at')
        if original_updated_at and student.get('updated_at'):
            if str(original_updated_at).strip() != str(student['updated_at']).strip():
                flash(
                    "Conflict detected: This student record was updated by another user while you were editing. "
                    "The form has been reloaded with the latest data to prevent accidental overwrites.",
                    "danger"
                )
                student_dict = dict(student)
                student_dict['photo_url'] = storage.get_url(student['photo_filename']) if student['photo_filename'] else None
                return render_template('edit_student.html', student=student_dict, departments=departments)

        name = request.form.get('name')
        register_number = request.form.get('register_number')
        department = request.form.get('department')
        year = request.form.get('year')
        email = request.form.get('email')
        marks = request.form.get('marks')
        phone = request.form.get('phone')
        dob = request.form.get('dob')
        photo_file = request.files.get('photo')
        remove_photo = request.form.get('remove_photo') == '1'

        is_valid, errors, cleaned = validate_student_data(
            name, register_number, department, email, marks,
            current_student_id=student_id, phone=phone, dob=dob, year=year
        )

        photo_filename = student['photo_filename']

        # Handle photo replacement or removal
        if remove_photo and photo_filename:
            storage.delete(photo_filename)
            photo_filename = None

        if photo_file and photo_file.filename:
            is_valid_img, img_err, sanitized_buf = validate_and_sanitize_image(photo_file)
            if not is_valid_img:
                errors.append(img_err or "Invalid photo file.")
            else:
                try:
                    if photo_filename:
                        storage.delete(photo_filename)
                    sanitized_buf.filename = photo_file.filename
                    photo_filename = storage.save(sanitized_buf)
                except Exception as e:
                    errors.append(f"Photo upload failed: {e!s}")

        if not is_valid or errors:
            for err in errors:
                flash(err, "danger")
            student_dict = dict(student)
            student_dict.update(request.form)
            student_dict['photo_url'] = storage.get_url(photo_filename) if photo_filename else None
            field_errors = map_errors_to_fields(errors)
            return render_template('edit_student.html', student=student_dict, departments=departments, field_errors=field_errors)

        user_id = session.get('user_id')
        username = session.get('username')
        ip_addr = request.remote_addr if request else "127.0.0.1"

        # Permission check if marks were modified
        curr_user = get_current_user()
        old_marks = float(student.get('marks') or 0.0)
        new_marks = float(cleaned.get('marks') or 0.0)
        if abs(old_marks - new_marks) > 1e-6:
            allowed, perm_err = check_staff_marks_permission(curr_user, student)
            if not allowed:
                return render_template('error.html', error_code=403, error_message=perm_err), 403

            # If admin override on published results
            if curr_user and curr_user.get('role') == 'admin' and is_semester_published():
                record_audit(
                    action="ADMIN_MARKS_OVERRIDE_AFTER_PUBLISH",
                    details=f"Admin overridden overall marks for '{cleaned['name']}' ({old_marks} -> {new_marks}) after semester results publication",
                    user_id=user_id,
                    username=username
                )

        StudentService.update_student(
            student_id=student_id,
            cleaned=cleaned,
            photo_filename=photo_filename,
            changed_by_id=user_id,
            changed_by_name=username or "system",
            reason="Student record update",
            ip_address=ip_addr
        )

        old_val = {
            'name': student['name'],
            'register_number': student['register_number'],
            'department': student['department'],
            'year': student['year'] if 'year' in student.keys() else 1,
            'email': student['email'],
            'marks': student['marks']
        }

        record_audit(
            action="STUDENT_UPDATED",
            details=f"Updated student record '{cleaned['name']}' ({cleaned['register_number']})",
            user_id=user_id,
            username=username,
            old_value=old_val,
            new_value=cleaned
        )

        flash(f"Student record for '{cleaned['name']}' has been successfully updated.", "success")
        return redirect(url_for('routes.view_student', student_id=student_id))

    student_dict = dict(student)
    student_dict['photo_url'] = storage.get_url(student['photo_filename']) if student['photo_filename'] else None
    return render_template('edit_student.html', student=student_dict, departments=departments)


# -----------------------------------------------------------------------------
# Student Records: Delete (Admin Only - RBAC)
# -----------------------------------------------------------------------------
@routes_bp.route('/students/<int:student_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_student(student_id):
    """
    Soft-delete a student record.
    Role-based: Restricted strictly to Administrators.
    """
    student = query_db("SELECT id, name, register_number FROM students WHERE id = ? AND deleted_at IS NULL", (student_id,), one=True)
    if not student:
        flash("Student record not found or already deleted.", "warning")
        return redirect(url_for('routes.list_students'))

    StudentService.soft_delete_student(student_id)

    user_id = session.get('user_id')
    record_audit(
        action="STUDENT_DELETED",
        details=f"Admin soft-deleted student '{student['name']}' (Reg: {student['register_number']}, ID: {student_id})",
        user_id=user_id,
        username=session.get('username')
    )

    flash(f"Student record for '{student['name']}' has been removed.", "info")
    return redirect(url_for('routes.list_students'))


# -----------------------------------------------------------------------------
# Export Student Records as CSV
# -----------------------------------------------------------------------------
@routes_bp.route('/students/export')
@login_required
@staff_required
def export_students_csv():
    """Export filtered student records to a downloadable CSV file."""
    search_query = (request.args.get('q') or '').strip()
    dept_filter = (request.args.get('dept') or '').strip()
    year_filter = request.args.get('year', type=int)
    grade_filter = (request.args.get('grade') or '').strip()
    status_filter = (request.args.get('status') or '').strip()

    where_clauses = ["s.deleted_at IS NULL"]
    params = []

    if search_query:
        where_clauses.append("(s.name LIKE ? OR s.register_number LIKE ? OR s.email LIKE ?)")
        wildcard = f"%{search_query}%"
        params.extend([wildcard, wildcard, wildcard])

    if dept_filter:
        where_clauses.append("s.department = ?")
        params.append(dept_filter)

    if year_filter:
        where_clauses.append("s.year = ?")
        params.append(year_filter)

    if grade_filter == 'O':
        where_clauses.append("s.marks >= 90.0")
    elif grade_filter == 'A+':
        where_clauses.append("s.marks >= 80.0 AND s.marks < 90.0")
    elif grade_filter == 'A':
        where_clauses.append("s.marks >= 70.0 AND s.marks < 80.0")
    elif grade_filter == 'B+':
        where_clauses.append("s.marks >= 60.0 AND s.marks < 70.0")
    elif grade_filter == 'B':
        where_clauses.append("s.marks >= 50.0 AND s.marks < 60.0")
    elif grade_filter == 'RA' or grade_filter == 'fail' or status_filter == 'fail':
        where_clauses.append("s.marks < 50.0")
    elif grade_filter == 'pass' or status_filter == 'pass':
        where_clauses.append("s.marks >= 50.0")
    elif grade_filter == 'distinction':
        where_clauses.append("s.marks >= 80.0")
    elif status_filter == 'pass':
        where_clauses.append("s.marks >= 50.0")
    elif status_filter == 'fail':
        where_clauses.append("s.marks < 50.0")

    where_sql = " AND ".join(where_clauses)

    students = query_db(
        f"""
        SELECT s.name, s.register_number, s.department, s.year, s.email, s.marks, s.cgpa,
               s.created_at, s.updated_at, sa.attendance_percentage
        FROM students s
        LEFT JOIN student_attendance sa ON s.id = sa.student_id
        WHERE {where_sql}
        ORDER BY s.department ASC, s.name ASC
        """,  # nosec B608
        params
    )

    output = io.StringIO()
    writer = csv.writer(output)

    # Header row
    writer.writerow([
        "Register Number", "Name", "Department", "Email",
        "Marks", "Grade", "Grade Point", "Status", "CGPA", "Attendance %", "Year", "Created At", "Updated At"
    ])

    for s in students:
        grade_letter, grade_point = get_grade_and_points(s['marks'])
        grade_info = calculate_grade(s['marks'])
        att_pct = f"{s['attendance_percentage']:.1f}%" if s.get('attendance_percentage') is not None else "--"
        cgpa_val = f"{s['cgpa']:.2f}" if s.get('cgpa') is not None else "--"
        status_val = grade_info.get('status', 'Pass' if grade_letter != 'RA' else 'Fail')
        writer.writerow([
            sanitize_csv_cell(s['register_number']),
            sanitize_csv_cell(s['name']),
            sanitize_csv_cell(s['department']),
            sanitize_csv_cell(s['email']),
            s['marks'],
            grade_letter,
            grade_point,
            status_val,
            cgpa_val,
            att_pct,
            s.get('year', 1),
            s['created_at'],
            s['updated_at']
        ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=student_records.csv"}
    )


# -----------------------------------------------------------------------------
# Export Student Records as Excel (.xlsx)
# -----------------------------------------------------------------------------
@routes_bp.route('/students/export-excel')
@login_required
@staff_required
def export_students_excel():
    """Export filtered student records to a downloadable Excel (.xlsx) workbook using openpyxl."""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    search_query = (request.args.get('q') or '').strip()
    dept_filter = (request.args.get('dept') or '').strip()
    year_filter = request.args.get('year', type=int)
    grade_filter = (request.args.get('grade') or '').strip()
    status_filter = (request.args.get('status') or '').strip()

    where_clauses = ["s.deleted_at IS NULL"]
    params = []

    if search_query:
        where_clauses.append("(s.name LIKE ? OR s.register_number LIKE ? OR s.email LIKE ?)")
        wildcard = f"%{search_query}%"
        params.extend([wildcard, wildcard, wildcard])

    if dept_filter:
        where_clauses.append("s.department = ?")
        params.append(dept_filter)

    if year_filter:
        where_clauses.append("s.year = ?")
        params.append(year_filter)

    if grade_filter == 'O':
        where_clauses.append("s.marks >= 90.0")
    elif grade_filter == 'A+':
        where_clauses.append("s.marks >= 80.0 AND s.marks < 90.0")
    elif grade_filter == 'A':
        where_clauses.append("s.marks >= 70.0 AND s.marks < 80.0")
    elif grade_filter == 'B+':
        where_clauses.append("s.marks >= 60.0 AND s.marks < 70.0")
    elif grade_filter == 'B':
        where_clauses.append("s.marks >= 50.0 AND s.marks < 60.0")
    elif grade_filter == 'RA' or grade_filter == 'fail' or status_filter == 'fail':
        where_clauses.append("s.marks < 50.0")
    elif grade_filter == 'pass' or status_filter == 'pass':
        where_clauses.append("s.marks >= 50.0")
    elif grade_filter == 'distinction':
        where_clauses.append("s.marks >= 80.0")
    elif status_filter == 'pass':
        where_clauses.append("s.marks >= 50.0")
    elif status_filter == 'fail':
        where_clauses.append("s.marks < 50.0")

    where_sql = " AND ".join(where_clauses)

    students = query_db(
        f"""
        SELECT s.name, s.register_number, s.department, s.year, s.email, s.marks, s.cgpa,
               s.created_at, s.updated_at, sa.attendance_percentage
        FROM students s
        LEFT JOIN student_attendance sa ON s.id = sa.student_id
        WHERE {where_sql}
        ORDER BY s.department ASC, s.name ASC
        """,  # nosec B608
        params
    )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Students"

    # Header styling
    header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
    headers = [
        "Register Number", "Name", "Department", "Year", "Email",
        "Marks", "Grade", "Grade Point", "Status", "CGPA", "Attendance %", "Created At"
    ]

    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    thin_border = Border(
        left=Side(style='thin', color='E2E8F0'),
        right=Side(style='thin', color='E2E8F0'),
        top=Side(style='thin', color='E2E8F0'),
        bottom=Side(style='thin', color='E2E8F0')
    )

    for row_idx, s in enumerate(students, start=2):
        grade_letter, grade_point = get_grade_and_points(s['marks'])
        grade_info = calculate_grade(s['marks'])
        row_data = [
            str(s['register_number']),
            str(s['name']),
            str(s['department']),
            int(s.get('year') or 1),
            str(s['email']),
            float(s['marks']),
            grade_letter,
            float(grade_point),
            grade_info.get('status', 'Pass' if grade_letter != 'RA' else 'Fail'),
            float(s['cgpa']) if s.get('cgpa') is not None else None,
            float(s['attendance_percentage']) if s.get('attendance_percentage') is not None else None,
            str(s['created_at'])
        ]
        ws.append(row_data)
        for col_idx in range(1, len(row_data) + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.border = thin_border
            if col_idx in (1, 4, 6, 7, 8, 9, 10, 11):
                cell.alignment = Alignment(horizontal="center")

    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return Response(
        output.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment;filename=student_records.xlsx"}
    )


# -----------------------------------------------------------------------------
# User Management (Admin Only - RBAC)
# -----------------------------------------------------------------------------
@routes_bp.route('/users', methods=['GET', 'POST'])
@login_required
@admin_required
def manage_users():
    """List system users and allow creation of new Staff or Admin accounts with departmental assignments."""
    departments = get_valid_departments()

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role')
        full_name = request.form.get('full_name')
        email = request.form.get('email')
        department = (request.form.get('department') or '').strip() or None
        assigned_subjects = (request.form.get('assigned_subjects') or '').strip().upper() or None

        is_valid, errors, cleaned = validate_user_data(username, password, role, full_name, email)

        if not is_valid:
            for err in errors:
                flash(err, "danger")
        else:
            hashed_pwd = generate_password_hash(cleaned['password'], method="pbkdf2:sha256")
            execute_db(
                """
                INSERT INTO users (username, password_hash, role, full_name, email, must_change_password, department, assigned_subjects)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (cleaned['username'], hashed_pwd, cleaned['role'], cleaned['full_name'], cleaned['email'], department, assigned_subjects),
                commit=True
            )

            record_audit(
                action="USER_CREATED",
                details=f"Admin created new {cleaned['role']} account for '{cleaned['username']}' (Dept: {department or 'All'}, Subjects: {assigned_subjects or 'All'})",
                user_id=session.get('user_id'),
                username=session.get('username')
            )

            flash(f"New {cleaned['role'].title()} account '{cleaned['username']}' created successfully (mandatory password change required on first login).", "success")
            return redirect(url_for('routes.manage_users'))

    page = max(1, request.args.get('page', 1, type=int))
    per_page = 10

    count_row = query_db("SELECT COUNT(*) as cnt FROM users", one=True)
    total_count = count_row['cnt'] if count_row else 0
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    if page > total_pages and total_count > 0:
        page = total_pages

    offset = (page - 1) * per_page
    users = query_db(
        "SELECT id, username, role, full_name, email, department, assigned_subjects, is_active, must_change_password, created_at FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (per_page, offset)
    )
    return render_template(
        'users.html',
        users=users,
        departments=departments,
        total_count=total_count,
        current_page=page,
        total_pages=total_pages,
        has_prev=page > 1,
        has_next=page < total_pages,
        prev_page=page - 1,
        next_page=page + 1
    )


# -----------------------------------------------------------------------------
# Audit Logs Viewer (Admin Only)
# -----------------------------------------------------------------------------
@routes_bp.route('/audit-logs')
@login_required
@admin_required
def audit_logs():
    """View system security audit trail with filtering and pagination (Phase 2)."""
    action_filter = (request.args.get('action') or '').strip()
    user_filter = (request.args.get('user') or '').strip()
    search_query = (request.args.get('q') or '').strip()

    page = max(1, request.args.get('page', 1, type=int))
    per_page = 20

    where_clauses = ["1=1"]
    params = []

    if action_filter:
        where_clauses.append("action = ?")
        params.append(action_filter)

    if user_filter:
        where_clauses.append("username = ?")
        params.append(user_filter)

    if search_query:
        where_clauses.append("(details LIKE ? OR username LIKE ? OR action LIKE ? OR old_value LIKE ? OR new_value LIKE ?)")
        wildcard = f"%{search_query}%"
        params.extend([wildcard, wildcard, wildcard, wildcard, wildcard])

    where_sql = " AND ".join(where_clauses)

    count_row = query_db(
        f"SELECT COUNT(*) as cnt FROM audit_logs WHERE {where_sql}",  # nosec B608
        list(params),
        one=True
    )
    total_count = count_row['cnt'] if count_row else 0
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    if page > total_pages and total_count > 0:
        page = total_pages

    offset = (page - 1) * per_page
    paged_params = list(params) + [per_page, offset]
    logs = query_db(
        f"SELECT * FROM audit_logs WHERE {where_sql} ORDER BY timestamp DESC LIMIT ? OFFSET ?",  # nosec B608
        paged_params
    )

    action_rows = query_db("SELECT DISTINCT action FROM audit_logs WHERE action IS NOT NULL ORDER BY action ASC")
    actions = [r['action'] for r in action_rows]

    return render_template(
        'audit_logs.html',
        logs=logs,
        actions=actions,
        selected_action=action_filter,
        selected_user=user_filter,
        search_query=search_query,
        total_count=total_count,
        current_page=page,
        total_pages=total_pages,
        has_prev=page > 1,
        has_next=page < total_pages,
        prev_page=page - 1,
        next_page=page + 1
    )


# -----------------------------------------------------------------------------
# Student Marksheet PDF Download
# -----------------------------------------------------------------------------
@routes_bp.route('/students/<int:student_id>/pdf')
@login_required
def download_student_pdf(student_id):
    """Generate and download official academic marksheet as PDF."""
    # S1: Students can only download their own grade card
    if session.get('role') == 'student':
        sid = current_student_id()
        if sid != student_id:
            return render_template(
                'error.html',
                error_code=403,
                error_message="Access Denied: You may only download your own grade card."
            ), 403

    student = query_db("SELECT * FROM students WHERE id = ? AND deleted_at IS NULL", (student_id,), one=True)
    if not student:
        flash("Student record not found.", "warning")
        return redirect(url_for('routes.list_students'))

    # Ensure student has a secure verify_token
    token = student.get('verify_token')
    if not token:
        token = generate_secure_token(24)
        execute_db("UPDATE students SET verify_token = ? WHERE id = ?", (token, student_id), commit=True)
        student = query_db("SELECT * FROM students WHERE id = ?", (student_id,), one=True)

    subjects = query_db(
        "SELECT * FROM student_subjects WHERE student_id = ? ORDER BY semester ASC, subject_code ASC",
        (student_id,)
    )
    attendance = query_db("SELECT * FROM student_attendance WHERE student_id = ?", (student_id,), one=True)

    verify_url = request.host_url.rstrip('/') + url_for('routes.verify_student_marksheet', token=token)
    pdf_buffer = generate_student_marksheet_pdf(dict(student), subjects=subjects, attendance=attendance, verification_url=verify_url)

    record_audit(
        action="PDF_MARKSHEET_DOWNLOADED",
        details=f"User downloaded PDF marksheet for student '{student['name']}' ({student['register_number']})",
        user_id=session.get('user_id'),
        username=session.get('username')
    )

    clean_reg = student['register_number'].replace('/', '_').replace('\\', '_')
    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{clean_reg}_grade_card.pdf"
    )


@routes_bp.route('/student/report-card.pdf')
@login_required
def download_student_own_pdf():
    """Allow logged-in student to download their own official PDF report card."""
    sid = current_student_id()
    if not sid:
        flash("No student record linked to your user account.", "danger")
        return redirect(url_for('routes.student_portal'))
    return download_student_pdf(sid)


# -----------------------------------------------------------------------------
# Print-Friendly Academic Report Card (Phase 4)
# -----------------------------------------------------------------------------
@routes_bp.route('/students/<int:student_id>/report-card')
@login_required
@staff_required
def view_student_report_card(student_id):
    """Render print-friendly institutional academic report card for a student."""
    student = query_db("SELECT * FROM students WHERE id = ? AND deleted_at IS NULL", (student_id,), one=True)
    if not student:
        flash("Student record not found.", "warning")
        return redirect(url_for('routes.list_students'))

    subjects = query_db(
        "SELECT * FROM student_subjects WHERE student_id = ? ORDER BY semester ASC, subject_code ASC",
        (student_id,)
    )
    attendance = query_db("SELECT * FROM student_attendance WHERE student_id = ?", (student_id,), one=True)
    att_info = get_attendance_status(attendance['attendance_percentage']) if attendance else None
    gpa_stats = calculate_gpa(subjects)
    semester_gpa_data = calculate_semester_and_overall_gpa(subjects)

    return render_template(
        'report_card.html',
        student=student,
        subjects=subjects,
        attendance=attendance,
        att_info=att_info,
        gpa_stats=gpa_stats,
        semester_gpa_data=semester_gpa_data
    )


@routes_bp.route('/student/report-card')
@login_required
def student_own_report_card_view():
    """Render print-friendly report card for logged-in student."""
    sid = current_student_id()
    if not sid:
        flash("No student record linked to your user account.", "danger")
        return redirect(url_for('routes.student_portal'))
    student = query_db("SELECT * FROM students WHERE id = ? AND deleted_at IS NULL", (sid,), one=True)
    if not student:
        flash("Student record not found.", "warning")
        return redirect(url_for('routes.student_portal'))

    subjects = query_db(
        "SELECT * FROM student_subjects WHERE student_id = ? ORDER BY semester ASC, subject_code ASC",
        (sid,)
    )
    attendance = query_db("SELECT * FROM student_attendance WHERE student_id = ?", (sid,), one=True)
    att_info = get_attendance_status(attendance['attendance_percentage']) if attendance else None
    gpa_stats = calculate_gpa(subjects)
    semester_gpa_data = calculate_semester_and_overall_gpa(subjects)

    return render_template(
        'report_card.html',
        student=student,
        subjects=subjects,
        attendance=attendance,
        att_info=att_info,
        gpa_stats=gpa_stats,
        semester_gpa_data=semester_gpa_data
    )


# -----------------------------------------------------------------------------
# Bulk Student Import via CSV
# -----------------------------------------------------------------------------
@routes_bp.route('/students/template.csv')
@routes_bp.route('/students/import/template.csv')
@login_required
@staff_required
def download_csv_template():
    """Provide a ready-to-use CSV template for bulk student onboarding."""
    csv_content = ImportService.generate_csv_template()
    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=student_import_template.csv"}
    )


@routes_bp.route('/students/template.xlsx')
@routes_bp.route('/students/import/template.xlsx')
@login_required
@staff_required
def download_excel_template():
    """Provide a ready-to-use formatted Excel (.xlsx) template for bulk student onboarding."""
    excel_buf = ImportService.generate_excel_template()
    return send_file(
        excel_buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="student_import_template.xlsx"
    )


@routes_bp.route('/students/import', methods=['GET', 'POST'])
@login_required
@staff_required
def import_students():
    """Bulk import student records from an uploaded CSV or Excel (.xlsx) file with preview support."""
    departments = get_valid_departments()
    user_id = session.get('user_id')

    if request.method == 'POST':
        action = request.form.get('action')

        # 1. User clicked "Confirm & Import Valid Records" from the preview screen
        if action == 'confirm':
            valid_rows = session.pop('bulk_import_valid_rows', None)
            if not valid_rows:
                flash("No validated student records were found in the session. Please upload your file again.", "warning")
                return redirect(url_for('routes.import_students'))

            inserted_count, skipped_count, errors = ImportService.commit_valid_import(valid_rows, user_id)
            record_audit(
                action="STUDENTS_BULK_IMPORTED",
                details=f"Bulk imported {inserted_count} student records after preview confirmation ({skipped_count} skipped)",
                user_id=user_id,
                username=session.get('username')
            )
            flash(f"Successfully imported {inserted_count} student record(s) and created portal accounts.", "success")
            if errors:
                for err in errors[:5]:
                    flash(f"Skipped: {err}", "warning")
                if len(errors) > 5:
                    flash(f"...and {len(errors) - 5} more records skipped.", "warning")
            return redirect(url_for('routes.list_students'))

        # 2. File uploaded for processing
        import_file = request.files.get('csv_file') or request.files.get('import_file')
        if not import_file or not import_file.filename:
            flash("Please select and upload a valid .csv or .xlsx file.", "danger")
            return render_template('import_students.html', departments=departments)

        filename = import_file.filename
        fname_lower = filename.lower()
        if not (fname_lower.endswith('.csv') or fname_lower.endswith('.xlsx') or fname_lower.endswith('.xls')):
            flash("Unsupported file format. Please upload a .csv or .xlsx file.", "danger")
            return render_template('import_students.html', departments=departments)

        preview_mode = request.form.get('preview_mode') == '1'
        parsed = ImportService.parse_and_validate_file(import_file, filename)

        if not parsed.get("success"):
            flash(parsed.get("error", "Failed to parse import file."), "danger")
            return render_template('import_students.html', departments=departments)

        valid_items = [r["cleaned"] for r in parsed["rows"] if r["status"] == "valid" and r.get("cleaned")]

        # If preview_mode is enabled, store in session and render preview page
        if preview_mode:
            session['bulk_import_valid_rows'] = valid_items
            return render_template('import_preview.html', rows=parsed["rows"], summary=parsed["summary"])

        # Otherwise, commit immediately (e.g. for direct test POSTs without preview_mode)
        inserted_count, skipped_count, errors = ImportService.commit_valid_import(valid_items, user_id)
        record_audit(
            action="STUDENTS_BULK_IMPORTED",
            details=f"Bulk imported {inserted_count} student records ({len(parsed['rows']) - inserted_count} skipped/errors)",
            user_id=user_id,
            username=session.get('username')
        )
        if inserted_count > 0:
            flash(f"Successfully imported {inserted_count} student record(s) and created portal accounts.", "success")

        all_skipped = [f"Row {r['row_number']} ({r['register_number']}): {'; '.join(r['errors'])}" for r in parsed["rows"] if r["status"] == "error"]
        all_skipped.extend(errors)
        if all_skipped:
            for s in all_skipped[:5]:
                flash(f"Skipped: {s}", "warning")
            if len(all_skipped) > 5:
                flash(f"...and {len(all_skipped) - 5} more rows skipped due to invalid entries.", "warning")

        if inserted_count > 0:
            return redirect(url_for('routes.list_students'))

    return render_template('import_students.html', departments=departments)


# -----------------------------------------------------------------------------
# User Account: Change Password
# -----------------------------------------------------------------------------
@routes_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    """Allow authenticated user to securely update their password."""
    if request.method == 'POST':
        current_password = request.form.get('current_password') or ''
        new_password = request.form.get('new_password') or ''
        confirm_password = request.form.get('confirm_password') or ''

        user_id = session.get('user_id')
        user = query_db("SELECT id, password_hash, session_version FROM users WHERE id = ?", (user_id,), one=True)

        if not user or not check_password_hash(user['password_hash'], current_password):
            flash("The current password you entered is incorrect.", "danger")
            return render_template('change_password.html')

        if new_password != confirm_password:
            flash("New password and confirmation password do not match.", "danger")
            return render_template('change_password.html')

        is_strong, pwd_err = validate_password_strength(new_password, username=session.get('username'))
        if not is_strong:
            flash(pwd_err, "danger")
            return render_template('change_password.html')

        if current_password == new_password:
            flash("New password cannot be the same as your current password.", "danger")
            return render_template('change_password.html')

        new_hash = generate_password_hash(new_password, method="pbkdf2:sha256")
        new_v = int(user.get('session_version') or 1) + 1
        execute_db(
            "UPDATE users SET password_hash = ?, session_version = ?, must_change_password = 0 WHERE id = ?",
            (new_hash, new_v, user_id),
            commit=True
        )
        session['session_version'] = new_v

        record_audit(
            action="PASSWORD_CHANGED",
            details=f"User '{session.get('username')}' updated their account password",
            user_id=user_id,
            username=session.get('username')
        )

        flash("Your account password has been updated successfully.", "success")
        if session.get('role') == 'student':
            return redirect(url_for('routes.student_portal'))
        return redirect(url_for('routes.dashboard'))

    return render_template('change_password.html')


# -----------------------------------------------------------------------------
# Student Attendance: Update (Staff/Admin)
# -----------------------------------------------------------------------------
@routes_bp.route('/students/<int:student_id>/attendance', methods=['POST'])
@login_required
@staff_required
def update_attendance(student_id):
    """Update student attendance numbers."""
    student = query_db("SELECT id, name FROM students WHERE id = ?", (student_id,), one=True)
    if not student:
        flash("Student record not found.", "warning")
        return redirect(url_for('routes.list_students'))

    try:
        total_classes = int(request.form.get('total_classes', 0))
        attended_classes = int(request.form.get('attended_classes', 0))
    except (ValueError, TypeError):
        flash("Total classes and attended classes must be valid numbers.", "danger")
        return redirect(url_for('routes.view_student', student_id=student_id))

    if total_classes <= 0:
        flash("Total classes must be greater than zero.", "danger")
        return redirect(url_for('routes.view_student', student_id=student_id))

    if attended_classes < 0 or attended_classes > total_classes:
        flash("Attended classes must be between 0 and total classes.", "danger")
        return redirect(url_for('routes.view_student', student_id=student_id))

    pct = round((attended_classes / total_classes) * 100, 2)

    existing = query_db("SELECT id FROM student_attendance WHERE student_id = ?", (student_id,), one=True)
    if existing:
        execute_db(
            """
            UPDATE student_attendance
            SET total_classes = ?, attended_classes = ?, attendance_percentage = ?, last_updated = CURRENT_TIMESTAMP
            WHERE student_id = ?
            """,
            (total_classes, attended_classes, pct, student_id),
            commit=True
        )
    else:
        execute_db(
            """
            INSERT INTO student_attendance (student_id, total_classes, attended_classes, attendance_percentage)
            VALUES (?, ?, ?, ?)
            """,
            (student_id, total_classes, attended_classes, pct),
            commit=True
        )

    record_audit(
        action="ATTENDANCE_UPDATED",
        details=f"Updated attendance for '{student['name']}': {attended_classes}/{total_classes} ({pct}%)",
        user_id=session.get('user_id'),
        username=session.get('username')
    )

    flash(f"Attendance updated: {attended_classes}/{total_classes} classes ({pct}%).", "success")
    return redirect(url_for('routes.view_student', student_id=student_id))


# -----------------------------------------------------------------------------
# Semester Subjects: Add / Delete (Staff/Admin)
# -----------------------------------------------------------------------------
@routes_bp.route('/students/<int:student_id>/add-subject', methods=['POST'])
@login_required
@staff_required
def add_subject(student_id):
    """Add a subject with marks and compute grade point / GPA."""
    student = query_db("SELECT id, name, department FROM students WHERE id = ?", (student_id,), one=True)
    if not student:
        flash("Student record not found.", "warning")
        return redirect(url_for('routes.list_students'))

    semester = request.form.get('semester')
    subject_code = (request.form.get('subject_code') or '').strip().upper()
    subject_name = (request.form.get('subject_name') or '').strip()
    credits_raw = request.form.get('credits')
    marks_raw = request.form.get('marks')

    try:
        sem = int(semester)
        crd = int(credits_raw)
        mrk = float(marks_raw)
    except (ValueError, TypeError):
        flash("Semester, credits, and marks must be valid numbers.", "danger")
        return redirect(url_for('routes.view_student', student_id=student_id))

    if sem < 1 or sem > 8:
        flash("Semester must be between 1 and 8.", "danger")
        return redirect(url_for('routes.view_student', student_id=student_id))

    if not subject_code or not subject_name:
        flash("Subject code and name are required.", "danger")
        return redirect(url_for('routes.view_student', student_id=student_id))

    if crd < 1 or crd > 10:
        flash("Credits must be between 1 and 10.", "danger")
        return redirect(url_for('routes.view_student', student_id=student_id))

    if mrk < 0.0 or mrk > 100.0:
        flash("Marks must be between 0 and 100.", "danger")
        return redirect(url_for('routes.view_student', student_id=student_id))

    # Phase 3: Staff permission and semester publish lock check
    curr_user = get_current_user()
    allowed, perm_err = check_staff_marks_permission(curr_user, student, subject_code=subject_code, semester=sem)
    if not allowed:
        return render_template('error.html', error_code=403, error_message=perm_err), 403

    letter, gp = get_grade_and_points(mrk)

    execute_db(
        """
        INSERT INTO student_subjects (student_id, semester, subject_code, subject_name, credits, marks, grade_point, letter_grade)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (student_id, sem, subject_code, subject_name, crd, mrk, gp, letter),
        commit=True
    )

    # Recalculate CGPA
    all_subjects = query_db("SELECT * FROM student_subjects WHERE student_id = ?", (student_id,))
    gpa_data = calculate_gpa(all_subjects)
    if gpa_data['gpa'] > 0:
        execute_db("UPDATE students SET cgpa = ? WHERE id = ?", (gpa_data['gpa'], student_id), commit=True)

    from backend.logger import record_marks_history
    record_marks_history(
        student_id=student_id,
        old_marks=0.0,
        new_marks=mrk,
        changed_by=session.get('user_id'),
        changed_by_name=session.get('username', 'system'),
        old_grade=None,
        new_grade=letter,
        subject_code=subject_code,
        subject_name=subject_name,
        reason=f"Added subject {subject_code} (Sem {sem})",
        ip_address=request.remote_addr
    )

    record_audit(
        action="SUBJECT_ADDED",
        details=f"Added subject '{subject_code} - {subject_name}' (Sem {sem}, Marks: {mrk}, Grade: {letter}) for '{student['name']}'",
        user_id=session.get('user_id'),
        username=session.get('username'),
        old_value=None,
        new_value={'subject_code': subject_code, 'marks': mrk, 'grade': letter, 'semester': sem}
    )

    dashboard_cache.invalidate()

    flash(f"Subject '{subject_code}' added successfully! (Grade: {letter}, GP: {gp})", "success")
    return redirect(url_for('routes.view_student', student_id=student_id))


@routes_bp.route('/students/<int:student_id>/delete-subject/<int:subject_id>', methods=['POST'])
@login_required
@staff_required
def delete_subject(student_id, subject_id):
    """Delete a subject from student's record and recalculate CGPA."""
    student = query_db("SELECT id, name, department FROM students WHERE id = ?", (student_id,), one=True)
    if not student:
        flash("Student record not found.", "warning")
        return redirect(url_for('routes.list_students'))

    # IDOR protection: Verify subject exists and belongs to student_id
    subject = query_db("SELECT * FROM student_subjects WHERE id = ?", (subject_id,), one=True)
    if not subject:
        flash("Subject record not found.", "warning")
        return redirect(url_for('routes.view_student', student_id=student_id))

    if subject['student_id'] != student_id:
        return render_template(
            'error.html',
            error_code=403,
            error_message="Access Denied: Subject record does not belong to the specified student."
        ), 403

    # Phase 3: Staff permission and semester publish lock check
    curr_user = get_current_user()
    allowed, perm_err = check_staff_marks_permission(curr_user, student, subject_code=subject['subject_code'], semester=subject['semester'])
    if not allowed:
        return render_template('error.html', error_code=403, error_message=perm_err), 403

    from backend.logger import record_marks_history
    record_marks_history(
        student_id=student_id,
        subject_id=subject_id,
        subject_code=subject['subject_code'],
        subject_name=subject['subject_name'],
        old_marks=subject['marks'],
        new_marks=0.0,
        changed_by=session.get('user_id'),
        changed_by_name=session.get('username', 'system'),
        old_grade=subject['letter_grade'],
        new_grade=None,
        reason="Subject removed",
        ip_address=request.remote_addr
    )

    execute_db("DELETE FROM student_subjects WHERE id = ? AND student_id = ?", (subject_id, student_id), commit=True)

    # Recalculate CGPA
    all_subjects = query_db("SELECT * FROM student_subjects WHERE student_id = ?", (student_id,))
    gpa_data = calculate_gpa(all_subjects)
    execute_db("UPDATE students SET cgpa = ? WHERE id = ?", (gpa_data['gpa'], student_id), commit=True)
    dashboard_cache.invalidate()

    record_audit(
        action="SUBJECT_DELETED",
        details=f"Deleted subject '{subject['subject_code']}' for '{student['name']}'",
        user_id=session.get('user_id'),
        username=session.get('username'),
        old_value={'subject_code': subject['subject_code'], 'marks': subject['marks']}
    )

    flash("Subject removed successfully.", "info")
    return redirect(url_for('routes.view_student', student_id=student_id))


# -----------------------------------------------------------------------------
# Email Marksheet Dispatch
# -----------------------------------------------------------------------------
@routes_bp.route('/students/<int:student_id>/email-marksheet', methods=['POST'])
@login_required
def email_marksheet(student_id):
    """Dispatch official PDF marksheet to student's registered email."""
    # S1 & IDOR Protection: Students can only email their own marksheet
    if session.get('role') == 'student':
        sid = current_student_id()
        if sid != student_id:
            return render_template(
                'error.html',
                error_code=403,
                error_message="Access Denied: You may only email your own marksheet."
            ), 403

    student = query_db("SELECT * FROM students WHERE id = ?", (student_id,), one=True)
    if not student:
        flash("Student record not found.", "warning")
        return redirect(url_for('routes.list_students'))

    subjects = query_db("SELECT * FROM student_subjects WHERE student_id = ? ORDER BY semester, subject_code", (student_id,))
    attendance = query_db("SELECT * FROM student_attendance WHERE student_id = ?", (student_id,), one=True)

    success, msg = send_student_marksheet_email(
        recipient_email=student['email'],
        student_name=student['name'],
        student_dict=dict(student),
        subjects=subjects,
        attendance=attendance
    )

    record_audit(
        action="EMAIL_MARKSHEET_DISPATCHED",
        details=f"Dispatched marksheet email to '{student['email']}' for '{student['name']}': {msg}",
        user_id=session.get('user_id'),
        username=session.get('username')
    )

    if success:
        flash(f"Marksheet sent to {student['email']}! ({msg})", "success")
    else:
        flash(f"Marksheet email status: {msg}", "warning")

    if session.get('role') == 'student':
        return redirect(url_for('routes.student_portal'))
    return redirect(url_for('routes.view_student', student_id=student_id))


# -----------------------------------------------------------------------------
# Student Portal (Dedicated Student Dashboard)
# -----------------------------------------------------------------------------
@routes_bp.route('/student/portal')
@routes_bp.route('/student-portal')
@login_required
def student_portal():
    """
    Dedicated dashboard view for logged-in students.
    Shows personal academic profile, semester subjects, attendance status & shortage warning.
    """
    user_id = session.get('user_id')
    user = query_db("SELECT * FROM users WHERE id = ?", (user_id,), one=True)

    student_id = user.get('student_id') if user else None
    if not student_id:
        if session.get('role') in ['admin', 'staff']:
            first = query_db("SELECT id FROM students ORDER BY id ASC LIMIT 1", one=True)
            if first:
                student_id = first['id']
            else:
                flash("No student records available to preview portal.", "info")
                return redirect(url_for('routes.dashboard'))
        else:
            flash("No student record linked to your account.", "danger")
            return redirect(url_for('auth.login'))

    student = query_db("SELECT * FROM students WHERE id = ?", (student_id,), one=True)
    if not student:
        flash("Student record not found.", "warning")
        return redirect(url_for('auth.login'))

    storage = get_storage()
    student_dict = dict(student)
    student_dict['grade_info'] = calculate_grade(student['marks'])
    student_dict['photo_url'] = storage.get_url(student['photo_filename']) if student['photo_filename'] else None

    # Attendance
    attendance = query_db("SELECT * FROM student_attendance WHERE student_id = ?", (student_id,), one=True)
    att_info = get_attendance_status(attendance['attendance_percentage']) if attendance else None

    # Subjects & GPA
    subjects = query_db(
        "SELECT * FROM student_subjects WHERE student_id = ? ORDER BY semester ASC, subject_code ASC",
        (student_id,)
    )
    gpa_stats = calculate_gpa(subjects)
    semester_gpa_data = calculate_semester_and_overall_gpa(subjects)

    # Group subjects by semester
    semesters = {}
    for sub in subjects:
        sem = sub['semester']
        if sem not in semesters:
            semesters[sem] = []
        semesters[sem].append(sub)

    return render_template(
        'student_portal.html',
        student=student_dict,
        attendance=attendance,
        att_info=att_info,
        subjects=subjects,
        semesters=semesters,
        gpa_stats=gpa_stats,
        semester_gpa_data=semester_gpa_data
    )


# -----------------------------------------------------------------------------
# Public Digital Marksheet Verification Portal (QR Code Endpoint)
# -----------------------------------------------------------------------------
@routes_bp.route('/verify/<string:token>')
def verify_student_marksheet(token):
    """
    Publicly accessible marksheet verification endpoint.
    Accessed when recruiters, employers, or examiners scan the QR code on the PDF grade card.
    Guarantees privacy (S4): Displays only student name, register number, department, and verified CGPA/grade.
    No photo, email, attendance, or internal subject scores leaked.
    """
    clean_token = token.strip()
    student = query_db(
        "SELECT * FROM students WHERE verify_token = ? AND deleted_at IS NULL",
        (clean_token,),
        one=True
    )
    if not student:
        return render_template('verify_student.html', verified=False), 404

    subjects = query_db("SELECT credits, grade_point FROM student_subjects WHERE student_id = ?", (student['id'],))
    gpa_stats = calculate_gpa([dict(s) for s in subjects])

    safe_student = {
        'name': student['name'],
        'register_number': student['register_number'],
        'department': student['department'],
        'cgpa': student.get('cgpa') or gpa_stats['gpa'],
        'grade_info': calculate_grade(student['marks'])
    }

    return render_template(
        'verify_student.html',
        verified=True,
        student=safe_student,
        gpa_stats=gpa_stats
    )


@routes_bp.route('/verify/reg/<string:reg_number>')
def verify_legacy_redirect(reg_number):
    """Legacy route disabled to prevent predictable register number enumeration attacks."""
    return render_template(
        'error.html',
        error_code=404,
        error_message="Verification via guessable register number is disabled for privacy protection."
    ), 404


# -----------------------------------------------------------------------------
# Phase 5 Feature 2: Edit Subject Route
# -----------------------------------------------------------------------------
# Phase 5 Feature 2: Edit Subject Route
# -----------------------------------------------------------------------------
@routes_bp.route('/students/<int:student_id>/subjects/<int:subject_id>/edit', methods=['GET', 'POST'])
@routes_bp.route('/students/<int:student_id>/edit-subject/<int:subject_id>', methods=['POST'])
@login_required
@staff_required
def edit_subject(student_id, subject_id):
    """Edit academic subject grade/credit evaluation with marks history tracking."""
    student = query_db("SELECT id, name, register_number, department FROM students WHERE id = ? AND deleted_at IS NULL", (student_id,), one=True)
    if not student:
        flash("Student record not found.", "warning")
        return redirect(url_for('routes.list_students'))

    subject = query_db("SELECT * FROM student_subjects WHERE id = ?", (subject_id,), one=True)
    if not subject:
        flash("Subject record not found.", "warning")
        return redirect(url_for('routes.view_student', student_id=student_id))

    # IDOR protection: Verify subject belongs to student_id
    if subject['student_id'] != student_id:
        return render_template(
            'error.html',
            error_code=403,
            error_message="Access Denied: Subject record does not belong to the specified student."
        ), 403

    if request.method == 'POST':
        # Phase 3: Staff permission and semester publish lock check
        curr_user = get_current_user()
        allowed, perm_err = check_staff_marks_permission(curr_user, student, subject_code=subject['subject_code'], semester=subject['semester'])
        if not allowed:
            return render_template('error.html', error_code=403, error_message=perm_err), 403

        semester = request.form.get('semester', type=int) or subject['semester']
        subject_code = (request.form.get('subject_code') or subject['subject_code']).strip().upper()
        subject_name = (request.form.get('subject_name') or subject['subject_name']).strip()
        credits = request.form.get('credits', type=int) or subject['credits']
        marks = request.form.get('marks', type=float)

        if not subject_code or not subject_name:
            flash("Subject Code and Subject Name are required.", "danger")
            return render_template('edit_subject.html', student=student, subject=subject)

        if credits is None or credits < 1 or credits > 10:
            flash("Credits must be between 1 and 10.", "danger")
            return render_template('edit_subject.html', student=student, subject=subject)

        if marks is None or marks < 0.0 or marks > 100.0:
            flash("Marks must be between 0.0 and 100.0.", "danger")
            return render_template('edit_subject.html', student=student, subject=subject)

        letter, gp = get_grade_and_points(marks)

        execute_db(
            """
            UPDATE student_subjects
            SET semester = ?, subject_code = ?, subject_name = ?, credits = ?, marks = ?,
                grade_point = ?, letter_grade = ?
            WHERE id = ? AND student_id = ?
            """,
            (semester, subject_code, subject_name, credits, marks, gp, letter, subject_id, student_id),
            commit=True
        )

        StudentService.recalculate_cgpa(student_id)
        dashboard_cache.invalidate()

        is_admin_override = curr_user and curr_user.get('role') == 'admin' and is_semester_published(semester)
        default_reason = f"Admin override after semester publishing for {subject_code}" if is_admin_override else f"Staff edited marks for {subject_code}"
        edit_reason = request.form.get('reason') or default_reason

        from backend.logger import record_marks_history
        record_marks_history(
            student_id=student_id,
            subject_id=subject_id,
            subject_code=subject_code,
            subject_name=subject_name,
            old_marks=subject['marks'],
            new_marks=marks,
            old_grade=subject['letter_grade'],
            new_grade=letter,
            changed_by=session.get('user_id'),
            changed_by_name=session.get('username', 'system'),
            reason=edit_reason,
            ip_address=request.remote_addr
        )

        action_name = "ADMIN_MARKS_OVERRIDE_AFTER_PUBLISH" if is_admin_override else "SUBJECT_UPDATED"
        record_audit(
            action=action_name,
            details=f"Updated subject '{subject_code}' (Sem {semester}, Marks: {marks}, Grade: {letter}) for '{student['name']}'",
            user_id=session.get('user_id'),
            username=session.get('username'),
            old_value={'marks': subject['marks'], 'grade': subject['letter_grade']},
            new_value={'marks': marks, 'grade': letter}
        )

        flash(f"Subject '{subject_code}' updated successfully! (Grade: {letter}, GP: {gp})", "success")
        return redirect(url_for('routes.view_student', student_id=student_id))

    return render_template('edit_subject.html', student=student, subject=subject)


# -----------------------------------------------------------------------------
# Phase 5 Feature 3: Soft-delete Trash Bin, Restore, and Hard-Delete (Admin Only)
# -----------------------------------------------------------------------------
@routes_bp.route('/students/trash')
@login_required
@admin_required
def trash_students():
    """View deleted student records eligible for recovery or permanent purge (with pagination - Phase 6)."""
    page = max(1, request.args.get('page', 1, type=int))
    per_page = 10

    count_row = query_db(
        "SELECT COUNT(*) as cnt FROM students WHERE deleted_at IS NOT NULL",
        one=True
    )
    total_count = count_row['cnt'] if count_row else 0
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    if page > total_pages and total_count > 0:
        page = total_pages

    offset = (page - 1) * per_page
    students = query_db(
        """
        SELECT s.id, s.name, s.register_number, s.department, s.email, s.marks, s.deleted_at,
               sa.attendance_percentage
        FROM students s
        LEFT JOIN student_attendance sa ON s.id = sa.student_id
        WHERE s.deleted_at IS NOT NULL
        ORDER BY s.deleted_at DESC
        LIMIT ? OFFSET ?
        """,
        (per_page, offset)
    )
    return render_template(
        'trash.html',
        students=students,
        total_count=total_count,
        current_page=page,
        total_pages=total_pages,
        has_prev=page > 1,
        has_next=page < total_pages,
        prev_page=page - 1,
        next_page=page + 1
    )


@routes_bp.route('/students/<int:student_id>/restore', methods=['POST'])
@login_required
@admin_required
def restore_student(student_id):
    """Restore a soft-deleted student record."""
    student = query_db("SELECT id, name, register_number FROM students WHERE id = ? AND deleted_at IS NOT NULL", (student_id,), one=True)
    if not student:
        flash("Deleted student record not found.", "warning")
        return redirect(url_for('routes.trash_students'))

    StudentService.restore_student(student_id)
    record_audit(
        action="STUDENT_RESTORED",
        details=f"Admin restored student '{student['name']}' ({student['register_number']})",
        user_id=session.get('user_id'),
        username=session.get('username')
    )
    flash(f"Student record for '{student['name']}' restored successfully.", "success")
    return redirect(url_for('routes.trash_students'))


@routes_bp.route('/students/<int:student_id>/hard-delete', methods=['POST'])
@login_required
@admin_required
def hard_delete_student(student_id):
    """Permanently purge a student record from the database."""
    student = query_db("SELECT id, name, register_number FROM students WHERE id = ?", (student_id,), one=True)
    if not student:
        flash("Student record not found.", "warning")
        return redirect(url_for('routes.trash_students'))

    StudentService.hard_delete_student(student_id)
    record_audit(
        action="STUDENT_HARD_DELETED",
        details=f"Admin permanently deleted student '{student['name']}' ({student['register_number']})",
        user_id=session.get('user_id'),
        username=session.get('username')
    )
    flash(f"Student record for '{student['name']}' permanently erased.", "info")
    return redirect(url_for('routes.trash_students'))


@routes_bp.route('/students/<int:student_id>/anonymize', methods=['POST'])
@login_required
@admin_required
def anonymize_student(student_id):
    """Permanently anonymize all Personally Identifiable Information (PII) for a student record."""
    student = query_db("SELECT id, name, register_number, deleted_at FROM students WHERE id = ?", (student_id,), one=True)
    if not student:
        flash("Student record not found.", "warning")
        return redirect(url_for('routes.list_students'))

    was_in_trash = student['deleted_at'] is not None
    success = StudentService.anonymize_student(student_id)
    if success:
        record_audit(
            action="STUDENT_ANONYMIZED",
            details=f"Admin permanently anonymized personal data for student ID {student_id} (formerly {student['register_number']})",
            user_id=session.get('user_id'),
            username=session.get('username')
        )
        flash(f"Personal identifiable information for student #{student_id} has been permanently anonymized.", "info")
    else:
        flash("Failed to anonymize student record.", "danger")

    if was_in_trash:
        return redirect(url_for('routes.trash_students'))
    return redirect(url_for('routes.list_students'))


# -----------------------------------------------------------------------------
# Phase 5 Feature 4: Daily Period-wise Attendance & Bulk Marking
# -----------------------------------------------------------------------------
@routes_bp.route('/attendance')
@login_required
@staff_required
def attendance_overview():
    """Daily period-wise attendance dashboard & bulk entry interface."""
    from datetime import date
    dept_filter = request.args.get('dept', '').strip()
    selected_date = request.args.get('date', date.today().isoformat()).strip()
    period = request.args.get('period', 1, type=int)
    subject_code = request.args.get('subject', '').strip().upper()

    departments = get_valid_departments()
    if not dept_filter and departments:
        dept_filter = departments[0]

    # Fetch available course subjects for selected department
    dept_subjects = query_db(
        """
        SELECT DISTINCT ss.subject_code, ss.subject_name
        FROM student_subjects ss
        JOIN students s ON ss.student_id = s.id
        WHERE s.department = ? AND s.deleted_at IS NULL
        ORDER BY ss.subject_code ASC
        """,
        (dept_filter,)
    )

    students = query_db(
        """
        SELECT s.id, s.name, s.register_number, s.department,
               ar.status as current_status
        FROM students s
        LEFT JOIN attendance_records ar
               ON s.id = ar.student_id AND ar.date = ? AND ar.period = ?
        WHERE s.department = ? AND s.deleted_at IS NULL
        ORDER BY s.register_number ASC
        """,
        (selected_date, period, dept_filter)
    )

    return render_template(
        'attendance_mark.html',
        departments=departments,
        selected_dept=dept_filter,
        selected_date=selected_date,
        selected_period=period,
        selected_subject=subject_code,
        dept_subjects=dept_subjects,
        students=students
    )


@routes_bp.route('/attendance/mark', methods=['POST'])
@login_required
@staff_required
def bulk_mark_attendance():
    """Submit daily period attendance in bulk and synchronize student_attendance percentages."""
    from datetime import date
    department = request.form.get('department')
    mark_date = request.form.get('date', date.today().isoformat())
    period = request.form.get('period', 1, type=int)
    subject_code = (request.form.get('subject_code') or 'GENERAL').strip().upper()

    user_id = session.get('user_id')
    marked_count = 0
    shortage_students = []

    students = query_db(
        "SELECT id, name FROM students WHERE department = ? AND deleted_at IS NULL",
        (department,)
    )

    for s in students:
        sid = s['id']
        status = request.form.get(f"status_{sid}")
        if status in ('present', 'absent', 'late', 'excused'):
            execute_db(
                """
                INSERT INTO attendance_records (student_id, date, period, status, subject_code, marked_by)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(student_id, date, period) DO UPDATE SET
                    status = excluded.status,
                    subject_code = excluded.subject_code,
                    marked_by = excluded.marked_by
                """,
                (sid, mark_date, period, status, subject_code, user_id),
                commit=False
            )
            marked_count += 1

            # Recalculate total classes & attended classes
            total_recs = query_db("SELECT COUNT(*) as cnt FROM attendance_records WHERE student_id = ?", (sid,), one=True)['cnt']
            attended_recs = query_db(
                "SELECT COUNT(*) as cnt FROM attendance_records WHERE student_id = ? AND status IN ('present', 'late', 'excused')",
                (sid,), one=True
            )['cnt']

            pct = round((attended_recs / total_recs * 100.0), 2) if total_recs > 0 else 0.0
            execute_db(
                """
                INSERT INTO student_attendance (student_id, total_classes, attended_classes, attendance_percentage)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(student_id) DO UPDATE SET
                    total_classes = excluded.total_classes,
                    attended_classes = excluded.attended_classes,
                    attendance_percentage = excluded.attendance_percentage,
                    last_updated = CURRENT_TIMESTAMP
                """,
                (sid, total_recs, attended_recs, pct),
                commit=False
            )
            if pct < 75.0:
                shortage_students.append(f"{s['name']} ({pct}%)")

    get_db().commit()
    dashboard_cache.invalidate()

    record_audit(
        action="ATTENDANCE_BULK_MARKED",
        details=f"Bulk marked attendance for {marked_count} students in '{department}' for {mark_date} (Period {period}, Subject: {subject_code})",
        user_id=user_id,
        username=session.get('username')
    )

    flash(f"Successfully recorded attendance for {marked_count} student(s) in {department} (Subject: {subject_code}).", "success")
    if shortage_students:
        shortage_names = ", ".join(shortage_students[:5])
        if len(shortage_students) > 5:
            shortage_names += f" and {len(shortage_students) - 5} more"
        flash(f"Warning: Low Attendance Shortage Alert (< 75%) detected for: {shortage_names}.", "warning")

    return redirect(url_for('routes.attendance_overview', dept=department, date=mark_date, period=period, subject=subject_code))


# -----------------------------------------------------------------------------
# Phase 5 Feature 5: Audit Log Filtering & CSV Export
# -----------------------------------------------------------------------------
@routes_bp.route('/audit-logs/export')
@login_required
@admin_required
def export_audit_logs_csv():
    """Download system security audit trail as sanitized CSV."""
    action_filter = request.args.get('action', '').strip()
    user_filter = request.args.get('username', '').strip()

    where = ["1=1"]
    params = []
    if action_filter:
        where.append("action = ?")
        params.append(action_filter)
    if user_filter:
        where.append("username = ?")
        params.append(user_filter)

    logs = query_db(
        f"SELECT * FROM audit_logs WHERE {' AND '.join(where)} ORDER BY timestamp DESC LIMIT 1000",  # nosec B608
        params
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Timestamp', 'Username', 'Action', 'IP Address', 'Details'])
    for log in logs:
        writer.writerow([
            log['id'],
            log['timestamp'],
            sanitize_csv_cell(log['username']),
            sanitize_csv_cell(log['action']),
            sanitize_csv_cell(log['ip_address'] or ''),
            sanitize_csv_cell(log['details'] or '')
        ])

    csv_data = output.getvalue()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_logs_export.csv"}
    )


# -----------------------------------------------------------------------------
# Phase 5 Feature 6: Student Self-Service Profile (Phone, Address, Photo only)
# -----------------------------------------------------------------------------
@routes_bp.route('/student/profile', methods=['GET', 'POST'])
@login_required
def student_self_profile():
    """Student self-service: update phone, address, and profile photo only."""
    user = get_current_user()
    sid = user.get('student_id')
    if not sid:
        flash("No student profile linked to your user account.", "warning")
        return redirect(url_for('routes.dashboard'))

    student = query_db("SELECT * FROM students WHERE id = ? AND deleted_at IS NULL", (sid,), one=True)
    if not student:
        flash("Student profile not found.", "danger")
        return redirect(url_for('routes.student_portal'))

    storage = get_storage()
    if request.method == 'POST':
        phone = (request.form.get('phone') or '').strip()
        address = (request.form.get('address') or '').strip()
        dob = (request.form.get('dob') or '').strip()
        photo_file = request.files.get('photo')

        if phone:
            is_valid_phone, phone_err = validate_phone_number(phone)
            if not is_valid_phone:
                flash(phone_err, "danger")
                return render_template('student_profile.html', student=student, photo_url=storage.get_url(student['photo_filename']))

        if dob:
            is_valid_dob, dob_err = validate_date_of_birth(dob)
            if not is_valid_dob:
                flash(dob_err, "danger")
                return render_template('student_profile.html', student=student, photo_url=storage.get_url(student['photo_filename']))

        photo_filename = student['photo_filename']
        if photo_file and photo_file.filename:
            is_valid_img, img_err, buf = validate_and_sanitize_image(photo_file)
            if not is_valid_img:
                flash(img_err or "Invalid photo file.", "danger")
                return render_template('student_profile.html', student=student, photo_url=storage.get_url(photo_filename))
            if photo_filename:
                storage.delete(photo_filename)
            buf.filename = photo_file.filename
            photo_filename = storage.save(buf)

        execute_db(
            """
            UPDATE students
            SET phone = ?, address = ?, dob = ?, photo_filename = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (phone or None, address or None, dob or None, photo_filename, sid),
            commit=True
        )

        record_audit(
            action="STUDENT_PROFILE_SELF_UPDATED",
            details=f"Student '{student['name']}' updated personal contact details / photo",
            user_id=user['id'],
            username=user['username']
        )

        flash("Your profile information has been updated successfully.", "success")
        return redirect(url_for('routes.student_portal'))

    photo_url = storage.get_url(student['photo_filename']) if student['photo_filename'] else None
    return render_template('student_profile.html', student=student, photo_url=photo_url)


# -----------------------------------------------------------------------------
# Phase 5 Feature 7: Semester Result Publishing Lock
# -----------------------------------------------------------------------------
@routes_bp.route('/settings/semester-lock', methods=['POST'])
@login_required
@admin_required
def toggle_semester_lock():
    """Admin toggle to lock or release published semester results, and dispatch student notifications."""
    semester = request.form.get('semester', type=int)
    key = f"semester_{semester}_published" if semester else "semester_results_published"

    current = query_db("SELECT value FROM system_settings WHERE key = ?", (key,), one=True)
    curr_val = current['value'] if current else ('0' if semester else '1')
    new_val = '0' if curr_val == '1' else '1'

    execute_db(
        """
        INSERT INTO system_settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """,
        (key, new_val),
        commit=True
    )

    sem_label = f"Semester {semester}" if semester else "All Semesters"
    status_str = "Published / Publicly Visible (Locked for Staff Changes)" if new_val == '1' else "Locked / Draft Mode"

    # When results are published (new_val == '1'), notify students
    email_count = 0
    if new_val == '1':
        if semester:
            students = query_db(
                """
                SELECT DISTINCT s.id, s.name, s.register_number, s.email, s.cgpa
                FROM students s
                JOIN student_subjects ss ON s.id = ss.student_id
                WHERE ss.semester = ? AND s.deleted_at IS NULL
                """,
                (semester,)
            )
        else:
            students = query_db("SELECT id, name, register_number, email, cgpa FROM students WHERE deleted_at IS NULL")

        for st in students:
            subjs = query_db("SELECT * FROM student_subjects WHERE student_id = ? AND semester = ?", (st['id'], semester or 1))
            gpa_res = calculate_sgpa(subjs) if subjs else {}
            st_sgpa = gpa_res.get('sgpa')
            try:
                send_marks_published_email(
                    to_email=st['email'],
                    student_name=st['name'],
                    register_number=st['register_number'],
                    semester=semester or 1,
                    sgpa=st_sgpa,
                    cgpa=st['cgpa']
                )
                email_count += 1
            except Exception:
                pass

    record_audit(
        action="SEMESTER_LOCK_TOGGLED",
        details=f"Admin set {sem_label} publishing status to '{status_str}' (Notifications sent: {email_count})",
        user_id=session.get('user_id'),
        username=session.get('username')
    )
    flash(
        f"{sem_label} results publishing status updated: {status_str}."
        + (f" Dispatched publication notification emails to {email_count} student(s)." if email_count > 0 else ""),
        "info"
    )
    return redirect(request.referrer or url_for('routes.dashboard'))


# -----------------------------------------------------------------------------
# Phase 5 Feature 8: Admin User Management (Deactivate & Reset Password)
# -----------------------------------------------------------------------------
@routes_bp.route('/users/<int:user_id>/toggle-status', methods=['POST'])
@login_required
@admin_required
def toggle_user_status(user_id):
    """Activate or deactivate a user account with safety checks (prevent deactivating last admin)."""
    target = query_db("SELECT id, username, role, is_active FROM users WHERE id = ?", (user_id,), one=True)
    if not target:
        flash("User account not found.", "warning")
        return redirect(url_for('routes.manage_users'))

    # Prevent deactivating the last active administrator
    if target['role'] == 'admin' and target['is_active']:
        active_admins = query_db("SELECT COUNT(*) as cnt FROM users WHERE role = 'admin' AND is_active = 1", one=True)['cnt']
        if active_admins <= 1:
            flash("Action Blocked: Cannot deactivate the only active system administrator.", "danger")
            return redirect(url_for('routes.manage_users'))

    new_status = 0 if target['is_active'] else 1
    execute_db(
        "UPDATE users SET is_active = ?, session_version = session_version + 1 WHERE id = ?",
        (new_status, user_id),
        commit=True
    )

    action_label = "Activated" if new_status else "Deactivated"
    record_audit(
        action=f"USER_{action_label.upper()}",
        details=f"Admin {action_label.lower()} user account '{target['username']}' (ID: {user_id})",
        user_id=session.get('user_id'),
        username=session.get('username')
    )
    flash(f"User account '{target['username']}' has been {action_label.lower()}.", "success")
    return redirect(url_for('routes.manage_users'))


@routes_bp.route('/users/<int:user_id>/reset-password', methods=['POST'])
@login_required
@admin_required
def admin_reset_user_password(user_id):
    """Admin resets a user's password, generating a temporary password and requiring change on next login."""
    target = query_db("SELECT id, username, full_name, email FROM users WHERE id = ?", (user_id,), one=True)
    if not target:
        flash("User account not found.", "warning")
        return redirect(url_for('routes.manage_users'))

    temp_pwd = generate_temp_password(14, username=target['username'])
    pwd_hash = generate_password_hash(temp_pwd, method="pbkdf2:sha256")

    execute_db(
        """
        UPDATE users
        SET password_hash = ?, must_change_password = 1, session_version = session_version + 1
        WHERE id = ?
        """,
        (pwd_hash, user_id),
        commit=True
    )

    record_audit(
        action="USER_PASSWORD_ADMIN_RESET",
        details=f"Admin reset password for user '{target['username']}'",
        user_id=session.get('user_id'),
        username=session.get('username')
    )

    flash(
        f"Password for user '{target['username']}' reset successfully! "
        f"Temporary Password: {temp_pwd} (provide this to the user; mandatory change required on login)",
        "success"
    )
    next_url = request.form.get('next') or request.args.get('next')
    if next_url and is_safe_redirect(next_url):
        return redirect(next_url)
    return redirect(url_for('routes.manage_users'))




