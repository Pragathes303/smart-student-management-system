"""
Flask Application Factory
Initializes Flask app, registers blueprints, configures CSRF protection,
rate-limiting, security headers, reverse proxy middleware, and error handlers.
"""

import os
import time
from datetime import timedelta

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from backend.api import api_bp
from backend.auth import auth_bp, get_current_user
from backend.cli import register_cli_commands
from backend.config import Config
from backend.limiter import limiter
from backend.logger import setup_logger
from backend.models import db
from backend.routes import routes_bp
from database.db import close_db

csrf = CSRFProtect()
migrate = Migrate()


def create_app(config_class=Config):
    """
    Factory function to initialize the Flask application.
    Configures templates and static files to reside in the frontend/ folder.
    """
    frontend_dir = os.path.join(config_class.BASE_DIR, 'frontend')
    template_folder = os.path.join(frontend_dir, 'templates')
    static_folder = os.path.join(frontend_dir, 'static')

    app = Flask(
        __name__,
        template_folder=template_folder,
        static_folder=static_folder
    )

    # Load configuration
    app.config.from_object(config_class)
    app.config.setdefault('START_TIME', time.time())

    # Startup security validation for production deployments (S8)
    env = str(app.config.get('ENV', 'development')).lower()
    secret = str(app.config.get('SECRET_KEY', ''))
    insecure_keys = [
        'replace-with-a-secure-random-key',
        'change-this',
        'default',
        'secret',
        'dev-key',
        'default-dev-secret-key-change-in-production'
    ]
    if env == 'production':
        if app.debug:
            raise RuntimeError("CRITICAL SECURITY VIOLATION: FLASK_DEBUG cannot be enabled in production environment.")
        if not secret or len(secret) < 32 or any(k in secret.lower() for k in insecure_keys):
            raise RuntimeError(
                "CRITICAL SECURITY CONFIGURATION: Production deployment requires a secure, "
                "unique SECRET_KEY of at least 32 characters in .env."
            )

    # Reverse proxy support (ProxyFix)
    trusted_proxies = app.config.get('TRUSTED_PROXIES')
    if trusted_proxies is None or (env == 'production' and trusted_proxies <= 0):
        trusted_proxies = 1 if env == 'production' else 0
    if trusted_proxies > 0:
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=trusted_proxies,
            x_proto=trusted_proxies,
            x_host=trusted_proxies,
            x_port=trusted_proxies,
            x_prefix=trusted_proxies
        )

    # Ensure required runtime directories exist
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(app.config['LOG_FILE'])), exist_ok=True)

    # Setup application logging
    setup_logger(app)

    # Initialize CSRF Protection (S3)
    csrf.init_app(app)
    # Programmatic API endpoints use Bearer tokens; session-authenticated mutating API calls require X-CSRFToken header in api_auth_required
    csrf.exempt(api_bp)

    # Initialize Flask-Limiter (S10)
    limiter.init_app(app)

    # Multi-worker rate limiting warning for production deployments
    rl_storage = str(app.config.get('RATELIMIT_STORAGE_URI', '')).strip().lower()
    if (env == 'production' or os.getenv('FLASK_ENV', '').lower() == 'production') and (not rl_storage or rl_storage.startswith('memory://')):
        app.logger.warning(
            "RATELIMIT_STORAGE_URI is set to 'memory://' in production. "
            "In multi-worker deployments (e.g. Gunicorn), rate limit counters and lockouts are isolated per worker. "
            "Configure a shared Redis instance (e.g. redis://...) via RATELIMIT_STORAGE_URI for cluster-wide enforcement."
        )

    # Configure SQLAlchemy & Flask-Migrate (Alembic)
    db_path = app.config['DATABASE_PATH']
    clean_db_path = os.path.abspath(db_path).replace('\\', '/')
    sqlite_uri = app.config.get('DATABASE_URL')
    if not sqlite_uri or sqlite_uri.startswith('sqlite:'):
        sqlite_uri = f"sqlite:///{clean_db_path}"
    elif sqlite_uri.startswith('postgres://'):
        sqlite_uri = sqlite_uri.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = sqlite_uri
    app.config.setdefault('SQLALCHEMY_TRACK_MODIFICATIONS', False)

    db.init_app(app)
    migrate.init_app(app, db)

    # Auto-initialize database schema if database file does not exist
    if not os.path.exists(db_path):
        app.logger.info("Database file not found at %s. Initializing schema...", db_path)
        from database.init_db import seed_database
        seed_database(db_path)

    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(routes_bp)
    app.register_blueprint(api_bp)

    # Register teardown function for database connections
    app.teardown_appcontext(close_db)

    # Context processor to inject global variables into all templates
    @app.context_processor
    def inject_globals():
        return {
            'current_user': get_current_user(),
            'app_title': 'Smart Student Management System',
            'storage_mode': app.config.get('STORAGE_BACKEND', 'local')
        }

    # Idle Session Timeout (Phase 1: 30 minutes idle timeout)
    @app.before_request
    def enforce_session_idle_timeout():
        if request.endpoint and request.endpoint.startswith('static'):
            return

        session.permanent = True
        timeout_val = app.config.get('PERMANENT_SESSION_LIFETIME', 1800)
        if isinstance(timeout_val, timedelta):
            timeout_seconds = int(timeout_val.total_seconds())
        else:
            timeout_seconds = int(timeout_val)
        app.permanent_session_lifetime = timedelta(seconds=timeout_seconds)

        if session.get('user_id'):
            now = time.time()
            last_active = session.get('_last_active')
            if last_active is not None and (now - last_active > timeout_seconds):
                session.clear()
                flash("Your session has timed out due to 30 minutes of inactivity. Please sign in again.", "warning")
                if request.endpoint not in ('auth.login', 'auth.logout') and not request.path.startswith('/api/'):
                    return redirect(url_for('auth.login'))
            session['_last_active'] = now

    # Security Headers (S12)
    @app.after_request
    def set_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        # CSP allowing local assets + pinned Chart.js & Google Fonts (until self-hosted in Phase 6)
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https:; "
            "object-src 'none';"
        )
        # HSTS in production deployment
        if app.config.get('ENV') == 'production' or os.getenv('FLASK_ENV') == 'production':
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains; preload'
        return response

    # Error Handlers
    @app.errorhandler(400)
    def bad_request(e):
        msg = getattr(e, 'description', "Bad Request - The submitted data or CSRF token was invalid.")
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Bad Request', 'message': str(msg)}), 400
        return render_template('error.html', error_code=400, error_message=str(msg)), 400

    @app.errorhandler(401)
    def unauthorized(e):
        msg = getattr(e, 'description', "Unauthorized - Valid authentication is required.")
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Unauthorized', 'message': str(msg)}), 401
        return render_template('error.html', error_code=401, error_message=str(msg)), 401

    @app.errorhandler(403)
    def forbidden(e):
        msg = getattr(e, 'description', "Access Denied - You do not have permissions to access this resource.")
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Forbidden', 'message': str(msg)}), 403
        return render_template('403.html', error_code=403, error_message=str(msg)), 403

    @app.errorhandler(404)
    def page_not_found(e):
        msg = getattr(e, 'description', "Page Not Found - The requested URL was not found on this server.")
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Not Found', 'message': str(msg)}), 404
        return render_template('404.html', error_code=404, error_message=str(msg)), 404

    @app.errorhandler(413)
    def request_entity_too_large(e):
        msg = "Uploaded file is too large. Maximum size is 3MB."
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Payload Too Large', 'message': msg}), 413
        return render_template('error.html', error_code=413, error_message=msg), 413

    @app.errorhandler(429)
    def ratelimit_exceeded(e):
        msg = getattr(e, 'description', "Rate limit exceeded. Please try again later.")
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Too Many Requests', 'message': str(msg)}), 429
        return render_template('error.html', error_code=429, error_message=str(msg)), 429

    @app.errorhandler(500)
    def internal_server_error(e):
        app.logger.error("Internal Server Error: %s", str(e))
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Internal Server Error', 'message': 'An unexpected error occurred.'}), 500
        return render_template('500.html', error_code=500, error_message="Internal Server Error - Something went wrong on the server."), 500

    # Register Administrative CLI Commands (Features 9 & 10)
    register_cli_commands(app)

    return app


if __name__ == '__main__':
    app = create_app()
    # Bound to loopback interface with configured debug setting (S8)
    app.run(host='127.0.0.1', port=5000, debug=app.config.get('DEBUG', False))
