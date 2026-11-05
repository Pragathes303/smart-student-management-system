"""
RESTful JSON API & OpenAPI / Swagger Documentation Module
Exposes versioned, authenticated endpoints under /api/v1/ and an interactive Swagger UI at /api/docs.
Supports both active session RBAC and cryptographic Bearer API Tokens (SHA-256 hashed).
"""

import hashlib
from functools import wraps

from flask import Blueprint, current_app, jsonify, render_template_string, request
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import validate_csrf

from backend.auth import get_current_user
from backend.limiter import limiter
from backend.logger import record_audit
from backend.security import generate_secure_token
from backend.storage import get_storage
from backend.utils import (
    calculate_gpa,
    calculate_grade,
    get_attendance_status,
    validate_student_data,
)
from database.db import execute_db, query_db

api_bp = Blueprint('api', __name__, url_prefix='/api')


def authenticate_api_request(allowed_roles=('admin', 'staff')):
    """
    Authenticate incoming API request via Bearer token or session.
    Returns: (is_authenticated: bool, result_or_response: dict | tuple)
    """
    auth_header = request.headers.get('Authorization', '').strip()
    
    # 1. Bearer Token Authentication
    if auth_header.startswith('Bearer '):
        raw_token = auth_header[7:].strip()
        token_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
        tok_record = query_db(
            "SELECT id, name, role, revoked FROM api_tokens WHERE token_hash = ?",
            (token_hash,),
            one=True
        )
        if not tok_record or tok_record['revoked']:
            return False, (jsonify({'error': 'Unauthorized', 'message': 'Invalid, expired, or revoked API Bearer token.'}), 401)
        
        if tok_record['role'] not in allowed_roles:
            return False, (jsonify({'error': 'Forbidden', 'message': f"Token role '{tok_record['role']}' not authorized for this resource."}), 403)
        
        return True, {'type': 'token', 'role': tok_record['role'], 'name': tok_record['name']}

    # 2. Session Authentication (for web-integrated API calls)
    user = get_current_user()
    if user:
        if user.get('role') not in allowed_roles:
            return False, (jsonify({'error': 'Forbidden', 'message': f"User role '{user.get('role')}' is not authorized."}), 403)

        # Mutating methods on /api/v1/* must reject session auth without valid CSRF
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            csrf_token = request.headers.get('X-CSRFToken') or request.headers.get('X-CSRF-Token')
            if not csrf_token:
                return False, (jsonify({'error': 'Forbidden', 'message': 'CSRF token missing in X-CSRFToken header for mutating session request.'}), 403)

            if current_app.config.get('WTF_CSRF_ENABLED', True):
                try:
                    validate_csrf(csrf_token)
                except Exception:
                    return False, (jsonify({'error': 'Forbidden', 'message': 'Invalid or expired CSRF token in X-CSRFToken header.'}), 403)

        return True, {'type': 'session', 'role': user['role'], 'user_id': user['id']}

    # 3. Unauthenticated rejection
    return False, (jsonify({'error': 'Unauthorized', 'message': 'API authentication required. Provide Bearer token or sign in.'}), 401)


def api_auth_required(allowed_roles=('admin', 'staff')):
    """Decorator requiring API authentication (Bearer Token or Authenticated Session)."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            ok, res = authenticate_api_request(allowed_roles)
            if not ok:
                return res
            return f(*args, **kwargs)
        return wrapper
    return decorator


def get_api_rate_limit_key():
    auth_header = request.headers.get('Authorization', '').strip()
    if auth_header.startswith('Bearer '):
        return f"token:{auth_header[7:].strip()}"
    return f"ip:{get_remote_address()}"


def is_api_rate_limit_exempt():
    if current_app.config.get('TESTING') and not current_app.config.get('TEST_RATE_LIMITER'):
        return True
    if request.path in ('/api/docs', '/api/v1/openapi.json', '/api/v1/health'):
        return True
    return False


limiter.limit(
    "60/minute",
    key_func=get_api_rate_limit_key,
    exempt_when=is_api_rate_limit_exempt
)(api_bp)


# -----------------------------------------------------------------------------
# System Health & Info (Public & Minimal)
# -----------------------------------------------------------------------------
@api_bp.route('/v1/health', methods=['GET'])
def health_check():
    """Public health check endpoint returning minimal operational status."""
    return jsonify({
        'status': 'healthy',
        'service': 'Smart Student Management System API',
        'version': '1.0.0'
    }), 200


# -----------------------------------------------------------------------------
# API Tokens Management (Admin Only)
# -----------------------------------------------------------------------------
@api_bp.route('/v1/tokens', methods=['GET'])
@api_auth_required(allowed_roles=('admin',))
def list_api_tokens():
    """List registered API tokens (admin only, hashes hidden)."""
    tokens = query_db(
        """
        SELECT t.id, t.name, t.role, t.created_at, t.revoked, u.username as creator
        FROM api_tokens t
        LEFT JOIN users u ON t.created_by = u.id
        ORDER BY t.created_at DESC
        """
    )
    return jsonify({'tokens': [dict(t) for t in tokens]}), 200


@api_bp.route('/v1/tokens', methods=['POST'])
@api_auth_required(allowed_roles=('admin',))
def create_api_token():
    """Generate a new secure API token (returned once in response)."""
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    role = (data.get('role') or 'staff').strip().lower()

    if not name or len(name) < 2:
        return jsonify({'error': 'Validation Failed', 'message': 'Token name is required (min 2 characters).'}), 422
    if role not in ('admin', 'staff'):
        return jsonify({'error': 'Validation Failed', 'message': "Token role must be 'admin' or 'staff'."}), 422

    raw_token = f"sms_{generate_secure_token(32)}"
    token_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()

    user = get_current_user()
    user_id = user['id'] if user else None

    ins = execute_db(
        """
        INSERT INTO api_tokens (token_hash, name, role, created_by, revoked)
        VALUES (?, ?, ?, ?, 0)
        """,
        (token_hash, name, role, user_id),
        commit=True
    )

    record_audit(
        action="API_TOKEN_CREATED",
        details=f"Admin created API token '{name}' with role '{role}'",
        user_id=user_id,
        username=user['username'] if user else "admin"
    )

    return jsonify({
        'message': 'API token created successfully. Store this token securely; it will not be displayed again.',
        'id': ins['lastrowid'],
        'name': name,
        'role': role,
        'token': raw_token
    }), 201


@api_bp.route('/v1/tokens/<int:token_id>', methods=['DELETE'])
@api_auth_required(allowed_roles=('admin',))
def revoke_api_token(token_id):
    """Revoke an API token."""
    execute_db("UPDATE api_tokens SET revoked = 1 WHERE id = ?", (token_id,), commit=True)
    return jsonify({'message': f'API token {token_id} revoked successfully.'}), 200


# -----------------------------------------------------------------------------
# Students: List & Filter (JSON)
# -----------------------------------------------------------------------------
@api_bp.route('/v1/students', methods=['GET'])
@api_auth_required(allowed_roles=('admin', 'staff'))
def api_list_students():
    """Retrieve list of students with optional search, department filter, and pagination."""
    search = (request.args.get('search') or '').strip()
    dept = (request.args.get('department') or '').strip()
    try:
        limit = min(100, max(1, int(request.args.get('limit', 50))))
        offset = max(0, int(request.args.get('offset', 0)))
    except (ValueError, TypeError):
        limit = 50
        offset = 0

    where_clauses = ["s.deleted_at IS NULL"]
    params = []

    if search:
        # Escape wildcard characters to prevent search exploitation
        clean_search = search.replace('%', '\\%').replace('_', '\\_')
        where_clauses.append("(s.name LIKE ? OR s.register_number LIKE ? OR s.email LIKE ?)")
        term = f"%{clean_search}%"
        params.extend([term, term, term])

    if dept:
        where_clauses.append("s.department = ?")
        params.append(dept)

    where_sql = " AND ".join(where_clauses)

    count_row = query_db(f"SELECT COUNT(*) as cnt FROM students s WHERE {where_sql}", params, one=True)  # nosec B608
    total_count = count_row['cnt'] if count_row else 0

    # Single JOIN query eliminating N+1 (B7)
    data_sql = f"""
        SELECT s.id, s.name, s.register_number, s.department, s.email, s.marks, s.cgpa,
               s.photo_filename, s.created_at, s.updated_at,
               sa.attendance_percentage
        FROM students s
        LEFT JOIN student_attendance sa ON s.id = sa.student_id
        WHERE {where_sql}
        ORDER BY s.name ASC
        LIMIT ? OFFSET ?
    """  # nosec B608
    rows = query_db(data_sql, params + [limit, offset])

    storage = get_storage()
    results = []
    for r in rows:
        item = dict(r)
        item['grade_info'] = calculate_grade(r['marks'])
        item['photo_url'] = storage.get_url(r['photo_filename']) if r['photo_filename'] else None
        results.append(item)

    return jsonify({
        'total': total_count,
        'count': len(results),
        'limit': limit,
        'offset': offset,
        'students': results
    }), 200


# -----------------------------------------------------------------------------
# Students: Single Detail (JSON)
# -----------------------------------------------------------------------------
@api_bp.route('/v1/students/<int:student_id>', methods=['GET'])
@api_auth_required(allowed_roles=('admin', 'staff'))
def api_get_student(student_id):
    """Retrieve complete academic dossier for a student by ID."""
    student = query_db("SELECT * FROM students WHERE id = ? AND deleted_at IS NULL", (student_id,), one=True)
    if not student:
        return jsonify({'error': 'Not Found', 'message': f'Student ID {student_id} not found'}), 404

    storage = get_storage()
    student_dict = dict(student)
    student_dict['grade_info'] = calculate_grade(student['marks'])
    student_dict['photo_url'] = storage.get_url(student['photo_filename']) if student['photo_filename'] else None

    # Attendance
    attendance = query_db("SELECT * FROM student_attendance WHERE student_id = ?", (student_id,), one=True)
    att_dict = dict(attendance) if attendance else None
    if att_dict:
        att_dict['status_info'] = get_attendance_status(att_dict['attendance_percentage'])

    # Semester Coursework & CGPA
    subjects = query_db("SELECT * FROM student_subjects WHERE student_id = ? ORDER BY semester, subject_code", (student_id,))
    subj_list = [dict(s) for s in subjects]
    gpa_stats = calculate_gpa(subj_list)

    return jsonify({
        'student': student_dict,
        'attendance': att_dict,
        'coursework': subj_list,
        'academic_standing': gpa_stats
    }), 200


# -----------------------------------------------------------------------------
# Students: Create Record (JSON POST)
# -----------------------------------------------------------------------------
@api_bp.route('/v1/students', methods=['POST'])
@api_auth_required(allowed_roles=('admin', 'staff'))
def api_create_student():
    """Create a new student record via JSON payload."""
    if not request.is_json:
        return jsonify({'error': 'Bad Request', 'message': 'Request payload must be JSON with Content-Type: application/json'}), 400

    data = request.get_json() or {}
    name = data.get('name')
    reg = data.get('register_number')
    dept = data.get('department')
    email = data.get('email')
    marks = data.get('marks')

    is_valid, errors, cleaned = validate_student_data(name, reg, dept, email, marks)
    if not is_valid:
        return jsonify({'error': 'Validation Failed', 'validation_errors': errors}), 422

    # Import and call central service (B1, B2)
    from backend.services.student_service import StudentService
    user = get_current_user()
    user_id = user['id'] if user else None

    student_id, student_record, generated_pwd = StudentService.create_student(
        cleaned=cleaned,
        photo_filename=None,
        created_by_user_id=user_id
    )

    record_audit(
        action="API_STUDENT_CREATED",
        details=f"API created student '{cleaned['name']}' ({cleaned['register_number']})",
        username=user['username'] if user else "api_client"
    )

    return jsonify({
        'message': 'Student created successfully',
        'id': student_id,
        'student': student_record,
        'portal_username': cleaned['register_number'].lower(),
        'one_time_password': generated_pwd
    }), 201


# -----------------------------------------------------------------------------
# Academic Analytics / Stats (JSON)
# -----------------------------------------------------------------------------
@api_bp.route('/v1/students/stats', methods=['GET'])
@api_bp.route('/v1/dashboard/stats', methods=['GET'])
@api_auth_required(allowed_roles=('admin', 'staff'))
def api_dashboard_stats():
    """Retrieve high-level institutional academic analytics and department distribution."""
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

    dept_rows = query_db(
        """
        SELECT department, COUNT(*) AS student_count, ROUND(AVG(marks), 2) AS avg_marks
        FROM students
        WHERE deleted_at IS NULL
        GROUP BY department
        ORDER BY student_count DESC
        """
    )

    return jsonify({
        'kpis': dict(kpis),
        'department_distribution': [dict(d) for d in dept_rows]
    }), 200


# -----------------------------------------------------------------------------
# OpenAPI 3.0 Specification (JSON)
# -----------------------------------------------------------------------------
@api_bp.route('/v1/openapi.json', methods=['GET'])
def openapi_spec():
    """Generate OpenAPI 3.0 schema definition for the system APIs."""
    spec = {
        "openapi": "3.0.0",
        "info": {
            "title": "Smart Student Management System API",
            "version": "1.0.0",
            "description": "Enterprise RESTful API with Bearer token authentication for student records and analytics."
        },
        "servers": [{"url": "/api", "description": "API Gateway"}],
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "Token"
                }
            }
        },
        "security": [{"bearerAuth": []}],
        "paths": {
            "/v1/health": {
                "get": {
                    "summary": "Health Check",
                    "security": [],
                    "responses": {"200": {"description": "Service operational"}}
                }
            },
            "/v1/students": {
                "get": {
                    "summary": "List Students",
                    "parameters": [
                        {"name": "search", "in": "query", "schema": {"type": "string"}},
                        {"name": "department", "in": "query", "schema": {"type": "string"}},
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 50}},
                        {"name": "offset", "in": "query", "schema": {"type": "integer", "default": 0}}
                    ],
                    "responses": {"200": {"description": "Student list"}, "401": {"description": "Unauthorized"}}
                },
                "post": {
                    "summary": "Create Student Record",
                    "responses": {"201": {"description": "Created"}, "401": {"description": "Unauthorized"}, "422": {"description": "Validation failed"}}
                }
            },
            "/v1/students/{student_id}": {
                "get": {
                    "summary": "Get Student Dossier",
                    "parameters": [{"name": "student_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "Success"}, "404": {"description": "Not Found"}}
                }
            },
            "/v1/students/stats": {
                "get": {
                    "summary": "Academic Analytics",
                    "responses": {"200": {"description": "KPI metrics"}}
                }
            }
        }
    }
    return jsonify(spec), 200


SWAGGER_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>API Documentation | Smart Student Management System</title>
    <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
    <style>
        body { margin: 0; background-color: #fafbfc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
        .topbar { display: none; }
        .swagger-ui .info { margin: 24px 0; }
        .swagger-ui .info .title { font-size: 28px; color: #1e3a8a; }
    </style>
</head>
<body>
    <div style="background-color: #1e3a8a; color: #ffffff; padding: 14px 24px; display: flex; justify-content: space-between; align-items: center;">
        <div style="font-weight: 700; font-size: 16px;">Smart Student Management System &bull; Interactive API Console</div>
        <a href="/" style="color: #cbd5e1; text-decoration: none; font-size: 13px;">&larr; Return to Web Dashboard</a>
    </div>
    <div id="swagger-ui"></div>
    <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script>
        window.onload = function() {
            SwaggerUIBundle({
                url: "/api/v1/openapi.json",
                dom_id: '#swagger-ui',
                deepLinking: true,
                presets: [
                    SwaggerUIBundle.presets.apis,
                    SwaggerUIBundle.SwaggerUIStandalonePreset
                ]
            });
        }
    </script>
</body>
</html>
"""

@api_bp.route('/docs')
def swagger_ui():
    """Render interactive Swagger UI for API exploration and testing."""
    return render_template_string(SWAGGER_TEMPLATE)
