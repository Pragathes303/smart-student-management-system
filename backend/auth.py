"""
Authentication and Role-Based Access Control (RBAC) Module
Handles secure user authentication, lockout protection, session invalidation,
safe redirects, and granular authorization decorators.
"""

import hmac
import os
import time
from functools import wraps

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from backend.limiter import clear_failed_logins, is_login_locked, limiter, record_failed_login
from backend.logger import record_audit
from backend.mailer import send_email_message
from backend.security import (
    generate_password_reset_token,
    is_safe_redirect,
    validate_password_strength,
    verify_password_reset_token_data,
)
from database.db import execute_db, query_db

auth_bp = Blueprint('auth', __name__)


def get_current_user():
    """
    Retrieve and validate the currently authenticated user from the database.
    Caches the user record in Flask request context `g` for query efficiency.
    Returns None if unauthenticated, deactivated, or if session_version is invalidated.
    """
    if getattr(g, '_current_user', None) is not None:
        return g._current_user

    user_id = session.get('user_id')
    if not user_id:
        g._current_user = None
        return None

    user = query_db(
        """
        SELECT id, username, role, full_name, email, student_id, is_active,
               must_change_password, session_version, department, assigned_subjects
        FROM users
        WHERE id = ?
        """,
        (user_id,),
        one=True
    )

    if not user:
        session.clear()
        g._current_user = None
        return None

    # Check if account has been deactivated
    if not user['is_active']:
        session.clear()
        g._current_user = None
        return None

    # Check if session has been invalidated by password change
    session_v = session.get('session_version')
    db_v = user.get('session_version')
    if session_v is not None and db_v is not None and int(db_v) != int(session_v):
        session.clear()
        g._current_user = None
        return None

    g._current_user = dict(user)
    return g._current_user


def current_student_id():
    """Return the student_id associated with the authenticated student user, or None."""
    user = get_current_user()
    if user and user.get('role') == 'student':
        return user.get('student_id')
    return None


def login_required(f):
    """Decorator requiring an active, verified, authenticated user session or valid METRICS_TOKEN."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Support Prometheus Bearer Token authentication for metrics endpoints
        if request.path in ('/metrics', '/api/v1/metrics'):
            auth_header = request.headers.get('Authorization', '').strip()
            metrics_token = os.getenv('METRICS_TOKEN') or current_app.config.get('METRICS_TOKEN')
            if auth_header.startswith('Bearer '):
                token = auth_header[7:].strip()
                if metrics_token and hmac.compare_digest(token, metrics_token):
                    return f(*args, **kwargs)
                if request.path == '/api/v1/metrics':
                    return jsonify({'error': 'Unauthorized', 'message': 'Invalid or missing METRICS_TOKEN'}), 401
                return Response("Unauthorized: Invalid or missing METRICS_TOKEN\n", status=401, mimetype="text/plain")

        user = get_current_user()
        if not user:
            flash("Please sign in to access this page.", "warning")
            # S7: Pass relative path instead of absolute URL to prevent open redirects
            next_url = request.full_path if request.full_path and request.full_path != '/?' else request.path
            return redirect(url_for('auth.login', next=next_url))

        # Enforce mandatory password change if flagged
        if user.get('must_change_password') and request.endpoint not in ('routes.change_password', 'auth.logout'):
            flash("You must update your password before accessing the system.", "warning")
            return redirect(url_for('routes.change_password'))

        return f(*args, **kwargs)
    return decorated_function


def role_required(allowed_roles):
    """
    Decorator requiring the authenticated user to hold one of the specified roles.
    Example: @role_required(['admin', 'staff'])
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = get_current_user()
            if not user:
                flash("Please sign in to continue.", "warning")
                next_url = request.full_path if request.full_path and request.full_path != '/?' else request.path
                return redirect(url_for('auth.login', next=next_url))

            if user.get('must_change_password') and request.endpoint not in ('routes.change_password', 'auth.logout'):
                flash("You must update your password before accessing the system.", "warning")
                return redirect(url_for('routes.change_password'))

            if user.get('role') not in allowed_roles:
                current_app.logger.warning(
                    "Security: Unauthorized role access attempt by user '%s' (role: %s) to %s",
                    user.get('username'), user.get('role'), request.path
                )
                record_audit(
                    action="UNAUTHORIZED_ACCESS",
                    details=f"User '{user.get('username')}' attempted unauthorized access to {request.path}",
                    user_id=user.get('id'),
                    username=user.get('username')
                )
                return render_template(
                    'error.html',
                    error_code=403,
                    error_message="Access Denied: You do not have permission to perform this action."
                ), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def admin_required(f):
    """
    Decorator requiring the authenticated user to hold the 'admin' role.
    Rejects unauthorized access with HTTP 403 Forbidden.
    """
    return role_required(['admin'])(f)


def staff_required(f):
    """
    Decorator requiring the authenticated user to hold an academic staff role ('staff' or 'admin').
    Rejects unauthorized access with HTTP 403 Forbidden.
    """
    return role_required(['admin', 'staff'])(f)


@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("20/minute", methods=['POST'], exempt_when=lambda: current_app.config.get('TESTING') and not current_app.config.get('TEST_RATE_LIMITER'))
def login():
    """User login endpoint with rate limiting, brute-force lockout, and safe redirects."""
    if get_current_user():
        if session.get('role') == 'student':
            return redirect(url_for('routes.student_portal'))
        return redirect(url_for('routes.dashboard'))

    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr or '127.0.0.1').split(',')[0].strip()

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip().lower()
        password = request.form.get('password') or ''

        if not username or not password:
            flash("Both username and password are required.", "danger")
            return render_template('login.html', username=username)

        # Check account lockout status (S10)
        locked, retry_after = is_login_locked(client_ip, username)
        if locked:
            record_audit(
                action="LOGIN_LOCKED_OUT",
                details=f"Login attempt blocked due to lockout for '{username}' (retry after {retry_after}s)",
                ip_address=client_ip,
                username=username
            )
            return render_template(
                'error.html',
                error_code=429,
                error_message=f"Too many login attempts. Account temporarily locked for {retry_after} seconds. Please try again later."
            ), 429

        user = query_db(
            """
            SELECT id, username, password_hash, role, full_name, email, is_active,
                   must_change_password, session_version
            FROM users
            WHERE LOWER(username) = LOWER(?) OR LOWER(email) = LOWER(?)
            """,
            (username, username),
            one=True
        )

        if user and user['is_active'] and check_password_hash(user['password_hash'], password):
            # Reset failed attempt counter upon successful login
            clear_failed_logins(client_ip, username)

            # Assign fresh session to prevent session fixation attacks
            session.clear()
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            session['full_name'] = user['full_name']
            session['session_version'] = user['session_version'] or 1
            session['_last_active'] = time.time()
            session.permanent = True

            record_audit(
                action="LOGIN_SUCCESS",
                details=f"User '{user['username']}' logged in with role '{user['role']}'",
                user_id=user['id'],
                username=user['username'],
                ip_address=client_ip
            )

            # S7: Validate next redirect parameter safely
            next_page = request.args.get('next')
            if next_page and is_safe_redirect(next_page):
                return redirect(next_page)

            if user.get('must_change_password'):
                flash("Please set a new password for your account to continue.", "info")
                return redirect(url_for('routes.change_password'))

            if user['role'] == 'student':
                return redirect(url_for('routes.student_portal'))
            return redirect(url_for('routes.dashboard'))

        else:
            record_failed_login(client_ip, username)
            record_audit(
                action="LOGIN_FAILED",
                details=f"Failed authentication attempt for username '{username}'",
                ip_address=client_ip,
                username=username
            )
            flash("Invalid credentials. (Invalid username or password)", "danger")
            return render_template('login.html', username=username)

    return render_template('login.html')


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    """
    Terminate the current user session.
    Strictly accepts POST requests to prevent cross-site request forgery logout attacks (S3).
    """
    user = get_current_user()
    if user:
        record_audit(
            action="LOGOUT",
            details=f"User '{user['username']}' logged out",
            user_id=user['id'],
            username=user['username']
        )

    session.clear()
    session.modified = True
    if hasattr(g, '_current_user'):
        del g._current_user
    flash("You have been successfully signed out.", "info")
    resp = redirect(url_for('auth.login'))
    cookie_name = current_app.config.get('SESSION_COOKIE_NAME', 'session')
    resp.delete_cookie(cookie_name)
    return resp


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
@limiter.limit(
    "5/15 minutes",
    methods=['POST'],
    exempt_when=lambda: current_app.config.get('TESTING') and not current_app.config.get('TEST_RATE_LIMITER')
)
def forgot_password():
    """Request password reset link via signed cryptographic token (Feature 1)."""
    if request.method == 'POST':
        identifier = (request.form.get('email') or '').strip().lower()
        if not identifier:
            flash("Please enter your registered email address or username.", "danger")
            return render_template('forgot_password.html')

        user = query_db(
            """
            SELECT id, username, email, session_version
            FROM users
            WHERE (LOWER(email) = LOWER(?) OR LOWER(username) = LOWER(?)) AND is_active = 1
            """,
            (identifier, identifier),
            one=True
        )
        if user and user.get('email'):
            token = generate_password_reset_token(user['id'], session_version=user['session_version'] or 1, secret_key=current_app.config['SECRET_KEY'])
            base_url = current_app.config.get('APP_BASE_URL', 'http://127.0.0.1:5000')
            reset_url = f"{base_url}/reset-password/{token}"
            body = f"""Hello {user['username']},

A password reset request was received for your Smart Student Management System account.
Please visit the link below to set a new password:

{reset_url}

This secure link will expire in 24 hours. If you did not request this, please ignore this message.
"""
            send_email_message(user['email'], "Password Reset Request - StudentMS", body)
            record_audit(
                action="PASSWORD_RESET_REQUESTED",
                details=f"Password reset token generated and sent to '{user['email']}'",
                user_id=user['id'],
                username=user['username']
            )

        # Anti-enumeration response: always show the same message
        flash("If your account is registered in our system, you will receive a password reset link shortly.", "info")
        return redirect(url_for('auth.login'))

    return render_template('forgot_password.html')


@auth_bp.route('/reset-password/<string:token>', methods=['GET', 'POST'])
@limiter.limit(
    "10/15 minutes",
    methods=['POST'],
    exempt_when=lambda: current_app.config.get('TESTING') and not current_app.config.get('TEST_RATE_LIMITER')
)
def reset_password(token):
    """Set new password using signed cryptographic token (Feature 1)."""
    token_data = verify_password_reset_token_data(token, current_app.config['SECRET_KEY'], max_age=86400)
    if not token_data or not token_data.get('user_id'):
        flash("The password reset link is invalid or has expired. Please request a new one.", "danger")
        return redirect(url_for('auth.forgot_password'))

    user_id = token_data['user_id']
    user = query_db("SELECT id, username, email, session_version FROM users WHERE id = ? AND is_active = 1", (user_id,), one=True)
    if not user:
        flash("Account not found or inactive.", "danger")
        return redirect(url_for('auth.login'))

    # Single-use validation: prevent token replay attacks
    token_version = token_data.get('v')
    if token_version is not None and user.get('session_version') is not None:
        if token_version != user['session_version']:
            flash("This password reset link has already been used or invalidated. Please request a new one.", "danger")
            return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        new_pwd = request.form.get('password') or ''
        confirm_pwd = request.form.get('confirm_password') or ''

        if new_pwd != confirm_pwd:
            flash("New passwords do not match.", "danger")
            return render_template('reset_password.html', token=token)

        is_valid, err_msg = validate_password_strength(new_pwd, username=user['username'])
        if not is_valid:
            flash(err_msg, "danger")
            return render_template('reset_password.html', token=token)

        pwd_hash = generate_password_hash(new_pwd, method="pbkdf2:sha256")
        execute_db(
            """
            UPDATE users
            SET password_hash = ?, session_version = COALESCE(session_version, 1) + 1, must_change_password = 0
            WHERE id = ?
            """,
            (pwd_hash, user_id),
            commit=True
        )

        record_audit(
            action="PASSWORD_RESET_SUCCESS",
            details=f"User '{user['username']}' successfully reset their password via token",
            user_id=user['id'],
            username=user['username']
        )

        flash("Your password has been successfully reset. Please sign in with your new password.", "success")
        return redirect(url_for('auth.login'))

    return render_template('reset_password.html', token=token)
