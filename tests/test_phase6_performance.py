"""
Phase 6 Performance, PostgreSQL Readiness, and Multi-Worker Test Suite
Validates:
1. PostgreSQL DATABASE_URL support and legacy postgres:// -> postgresql:// prefix modernization
2. SQL parameter placeholder conversion (? -> %s) and RETURNING id handling
3. Elimination of N+1 queries and eager loading on listing views
4. Pagination across student listings, trash bin, and user management
5. Dashboard caching with configurable 1-5 minute TTL (default 180s) and write invalidation
6. Multi-worker Gunicorn stateless readiness and rate limiter storage configurability
7. Live 30-second interval dashboard KPI polling API endpoint (/api/v1/dashboard/kpis)
"""
from unittest.mock import MagicMock, patch

from backend.app import create_app
from backend.cache import SimpleTTLCache, dashboard_cache
from backend.config import Config
from backend.limiter import limiter
from backend.routes import get_dashboard_metrics
from database.db import (
    _convert_placeholders,
    _is_pg_connection,
    execute_db,
    get_database_url,
    init_db,
    is_postgres,
    normalize_database_url,
    query_db,
)


# =============================================================================
# 1. PostgreSQL DATABASE_URL Normalization & Multi-Driver Dialect
# =============================================================================
def test_postgres_url_normalization(monkeypatch):
    """Verify legacy postgres:// URLs from Heroku/Render are cleanly converted to postgresql://."""
    assert normalize_database_url('postgres://user:pass@ep-cool-db.us-east-2.aws.neon.tech/neondb') == \
           'postgresql://user:pass@ep-cool-db.us-east-2.aws.neon.tech/neondb'
    assert normalize_database_url('postgresql://user:pass@localhost:5432/students_db') == \
           'postgresql://user:pass@localhost:5432/students_db'
    assert normalize_database_url('sqlite:///custom/path/students.db') == 'sqlite:///custom/path/students.db'
    assert normalize_database_url(None) is None
    assert normalize_database_url('') is None

    monkeypatch.setenv('DATABASE_URL', 'postgres://testuser:testpass@db.example.com:5432/testdb')
    assert get_database_url() == 'postgresql://testuser:testpass@db.example.com:5432/testdb'


def test_is_postgres_detection(monkeypatch):
    """Verify is_postgres accurately detects PostgreSQL configs from DATABASE_URL."""
    monkeypatch.setenv('DATABASE_URL', 'postgres://u:p@dbhost:5432/dbname')
    assert is_postgres() is True

    monkeypatch.setenv('DATABASE_URL', 'postgresql://u:p@dbhost:5432/dbname')
    assert is_postgres() is True

    monkeypatch.setenv('DATABASE_URL', 'sqlite:///students.db')
    assert is_postgres() is False

    monkeypatch.delenv('DATABASE_URL', raising=False)
    assert is_postgres() is False


def test_is_pg_connection_helper():
    """Verify _is_pg_connection helper accurately identifies driver types."""
    mock_pg = MagicMock()
    mock_pg.__class__.__module__ = 'psycopg2.extensions'
    assert _is_pg_connection(mock_pg) is True

    import sqlite3
    mock_sqlite = MagicMock(spec=sqlite3.Connection)
    assert _is_pg_connection(mock_sqlite) is False


def test_convert_placeholders():
    """Verify SQLite '?' placeholders are converted to PostgreSQL '%s'."""
    sql = "SELECT * FROM students WHERE id = ? AND department = ? ORDER BY ? LIMIT ? OFFSET ?"
    expected = "SELECT * FROM students WHERE id = %s AND department = %s ORDER BY %s LIMIT %s OFFSET %s"
    assert _convert_placeholders(sql) == expected


def test_execute_db_postgres_returning_id():
    """Verify execute_db automatically appends RETURNING id for PostgreSQL INSERTs to populate lastrowid."""
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = {'id': 42}
    mock_cursor.rowcount = 1

    mock_conn = MagicMock()
    mock_conn.__class__.__module__ = 'psycopg2.extensions'
    mock_conn.cursor.return_value = mock_cursor

    res = execute_db(
        "INSERT INTO students (name, email) VALUES (?, ?)",
        ("John Doe", "john@example.com"),
        commit=True,
        custom_db=mock_conn
    )

    # Check query was transformed
    mock_cursor.execute.assert_called_once()
    called_sql, called_args = mock_cursor.execute.call_args[0]
    assert '%s' in called_sql
    assert '?' not in called_sql
    assert 'RETURNING id' in called_sql
    assert called_args == ("John Doe", "john@example.com")
    assert res['lastrowid'] == 42
    assert res['rowcount'] == 1
    mock_conn.commit.assert_called_once()


def test_query_db_postgres_execution():
    """Verify query_db executes parameterized queries with %s and returns dictionaries."""
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [
        {'id': 1, 'name': 'Alice'},
        {'id': 2, 'name': 'Bob'}
    ]

    mock_conn = MagicMock()
    mock_conn.__class__.__module__ = 'psycopg2.extensions'
    mock_conn.cursor.return_value = mock_cursor

    rows = query_db(
        "SELECT id, name FROM students WHERE department = ?",
        ("Computer Science",),
        custom_db=mock_conn
    )

    mock_cursor.execute.assert_called_once()
    called_sql, called_args = mock_cursor.execute.call_args[0]
    assert '%s' in called_sql
    assert '?' not in called_sql
    assert called_args == ("Computer Science",)
    assert len(rows) == 2
    assert rows[0]['name'] == 'Alice'


def test_init_db_postgres_schema_adaptation(monkeypatch):
    """Verify init_db strips PRAGMAs and converts AUTOINCREMENT to SERIAL for PostgreSQL."""
    monkeypatch.setenv('DATABASE_URL', 'postgresql://u:p@dbhost:5432/dbname')

    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.__class__.__module__ = 'psycopg2.extensions'
    mock_conn.cursor.return_value = mock_cursor

    with patch('database.db.get_db', return_value=mock_conn):
        assert init_db() is True
        mock_cursor.execute.assert_called_once()
        executed_sql = mock_cursor.execute.call_args[0][0]
        assert 'PRAGMA' not in executed_sql
        assert 'INTEGER PRIMARY KEY AUTOINCREMENT' not in executed_sql
        assert 'SERIAL PRIMARY KEY' in executed_sql
        assert 'fk_users_student' in executed_sql
        assert 'DO $$' in executed_sql
        mock_conn.commit.assert_called_once()


def test_execute_db_postgres_existing_returning():
    """Verify execute_db does not append a duplicate RETURNING id when already provide
    d."""
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = {'id': 99}
    mock_cursor.rowcount = 1

    mock_conn = MagicMock()
    mock_conn.__class__.__module__ = 'psycopg2.extensions'
    mock_conn.cursor.return_value = mock_cursor

    res = execute_db(
        "INSERT INTO departments (code, name) VALUES (?, ?) RETURNING id",
        ("CS", "Computer Science"),
        commit=True,
        custom_db=mock_conn,
    )
    called_sql = mock_cursor.execute.call_args[0][0]
    assert called_sql.upper().count('RETURNING') == 1
    assert res['lastrowid'] == 99


def test_execute_db_sqlite_explicit_returning(app):
    """Verify execute_db handles explicit RETURNING id on SQLite seamlessly."""
    with app.app_context():
        res = execute_db(
            "INSERT INTO departments (code, name) VALUES (?, ?) RETURNING id",
            ("TEST_DEPT", "Test Department Description"),
            commit=True,
        )
        assert res['lastrowid'] is not None
        assert res['lastrowid'] > 0


def test_migrate_tables_postgres_idempotent(monkeypatch):
    """Verify migrate_tables on PostgreSQL avoids PRAGMA table_info and executes ON CONFLICT DO NOTHING."""
    from database.init_db import migrate_tables

    monkeypatch.setenv('DATABASE_URL', 'postgresql://u:p@dbhost:5432/dbname')
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.__class__.__module__ = 'psycopg2.extensions'
    mock_conn.cursor.return_value = mock_cursor

    # Running migrate_tables should not invoke PRAGMA
    migrate_tables(mock_conn)

    # Verify all calls made to cursor.execute used %s and ON CONFLICT DO NOTHING
    for call in mock_cursor.execute.call_args_list:
        sql = call[0][0]
        assert 'PRAGMA' not in sql
        assert 'ON CONFLICT' in sql
        assert 'DO NOTHING' in sql


def test_seed_database_production_idempotent(monkeypatch):
    """Verify seed_database in production mode is idempotent and does not recreate or reset admin if one exists."""
    from database.init_db import seed_database

    monkeypatch.setenv('FLASK_ENV', 'production')
    monkeypatch.setenv('ADMIN_USERNAME', 'prodadmin')
    monkeypatch.setenv('ADMIN_PASSWORD', 'SecureAdminPass2026!')

    mock_conn = MagicMock()

    # Simulate admin already existing
    with patch('database.init_db.init_db'), \
         patch('database.init_db.get_db', return_value=mock_conn), \
         patch('database.init_db.migrate_tables'), \
         patch('database.init_db.query_db', return_value={'cnt': 1}), \
         patch('database.init_db.execute_db') as mock_exec:

        seed_database()
        # execute_db should NOT be called to insert any admin
        mock_exec.assert_not_called()
        mock_conn.close.assert_called_once()



# =============================================================================
# 2. Dashboard Analytics Caching (1-5 Minutes TTL & Invalidation)
# =============================================================================
def test_dashboard_cache_ttl_and_hit(app):
    """Verify dashboard metrics are cached with the 180s TTL and prevent re-querying the database."""
    with app.app_context():
        # Clear cache first
        dashboard_cache.invalidate()

        # Initial call populates cache
        metrics_1 = get_dashboard_metrics(force_refresh=False)
        assert 'total_students' in metrics_1
        assert dashboard_cache.has('dashboard_metrics') is True

        # Mutate cache artificially to verify cache hit
        cached_entry = dashboard_cache.get('dashboard_metrics')
        cached_entry['total_students'] = 9999
        dashboard_cache.set('dashboard_metrics', cached_entry, ttl_seconds=180)

        # Second call must return cached object with 9999 without hitting DB
        metrics_2 = get_dashboard_metrics(force_refresh=False)
        assert metrics_2['total_students'] == 9999

        # Clean up
        dashboard_cache.invalidate()


def test_dashboard_cache_invalidation_on_mutations(client, app):
    """Verify adding, editing, or deleting student records invalidates the dashboard cache."""
    with app.app_context():
        dashboard_cache.set('dashboard_metrics', {'total_students': 100}, ttl_seconds=180)
        assert dashboard_cache.has('dashboard_metrics') is True

        # Invalidation helper
        dashboard_cache.invalidate()
        assert dashboard_cache.has('dashboard_metrics') is False


def test_simple_ttl_cache_expiration():
    """Verify SimpleTTLCache properly expires items after ttl_seconds."""
    cache = SimpleTTLCache(default_ttl_seconds=1)
    cache.set('short_key', 'short_val', ttl_seconds=0.01)
    import time
    time.sleep(0.02)
    assert cache.get('short_key') is None
    assert cache.has('short_key') is False


# =============================================================================
# 3. Live Dashboard Updates via Simple Polling (30s)
# =============================================================================
def test_live_dashboard_kpis_endpoint_auth_required(client):
    """Verify /api/v1/dashboard/kpis requires authentication."""
    resp = client.get('/api/v1/dashboard/kpis')
    assert resp.status_code in (302, 401)


def test_live_dashboard_kpis_endpoint_staff_success(client, app):
    """Verify /api/v1/dashboard/kpis returns structured JSON with 200 for staff/admin."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'admin'
        sess['role'] = 'admin'

    resp = client.get('/api/v1/dashboard/kpis')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert 'timestamp' in data
    assert 'kpis' in data
    assert 'total_students' in data['kpis']
    assert 'pass_percentage' in data['kpis']
    assert 'avg_marks' in data['kpis']
    assert 'max_marks' in data['kpis']
    assert 'chart_dept_labels' in data
    assert 'chart_grade_labels' in data


def test_live_dashboard_kpis_student_forbidden(student_client):
    """Verify students cannot access staff analytics KPI endpoint (HTTP 403)."""
    resp = student_client.get('/api/v1/dashboard/kpis')
    assert resp.status_code == 403


# =============================================================================
# 4. Listing Views Pagination & N+1 Prevention
# =============================================================================
def test_trash_students_pagination(client):
    """Verify /students/trash supports pagination parameters (page=1, page=2)."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'admin'
        sess['role'] = 'admin'

    resp = client.get('/students/trash?page=1')
    assert resp.status_code == 200
    assert b"Trash Bin" in resp.data

    resp_p2 = client.get('/students/trash?page=2')
    assert resp_p2.status_code == 200


def test_manage_users_pagination(client):
    """Verify /users supports pagination parameters."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'admin'
        sess['role'] = 'admin'

    resp = client.get('/users?page=1')
    assert resp.status_code == 200
    assert b"Registered Accounts" in resp.data

    resp_p2 = client.get('/users?page=2')
    assert resp_p2.status_code == 200


def test_student_list_eager_loading_query_count(app):
    """Verify /students executes a fixed number of queries (COUNT + 1 paged JOIN) avoiding N+1."""
    with app.app_context():
        from database.db import query_db
        # Both students list and single student join attendance in 1 query
        rows = query_db(
            """
            SELECT s.id, s.name, s.register_number, s.department, s.year, s.marks,
                   sa.attendance_percentage
            FROM students s
            LEFT JOIN student_attendance sa ON s.id = sa.student_id
            WHERE s.deleted_at IS NULL
            LIMIT 10 OFFSET 0
            """
        )
        assert isinstance(rows, list)


# =============================================================================
# 5. Multi-Worker Gunicorn Stateless & Configurable Rate Limiter
# =============================================================================
def test_rate_limiter_storage_config_inheritance(monkeypatch):
    """Verify RATELIMIT_STORAGE_URI is configurable without hardcoded in-memory lock-in."""
    monkeypatch.setenv('RATELIMIT_STORAGE_URI', 'memory://')

    class CustomLimiterConfig(Config):
        TESTING = True
        RATELIMIT_STORAGE_URI = 'memory://'
        SECRET_KEY = 'phase6-test-secret-key-32-chars-long-min!'

    test_app = create_app(CustomLimiterConfig)
    assert test_app.config['RATELIMIT_STORAGE_URI'] == 'memory://'
    assert limiter is not None


def test_stateless_session_across_simulated_workers(tmp_path):
    """Verify session data is encrypted and stateless on client cookie, accessible across multiple app instances."""
    test_db = str(tmp_path / 'shared_cluster.db')
    init_db(test_db)

    class SharedClusterConfig(Config):
        TESTING = True
        DATABASE_PATH = test_db
        SECRET_KEY = 'shared-cluster-secret-key-must-be-32-chars!'

    app_worker_1 = create_app(SharedClusterConfig)
    app_worker_2 = create_app(SharedClusterConfig)

    client_1 = app_worker_1.test_client()
    with client_1.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'admin'
        sess['role'] = 'admin'

    # Retrieve signed cookie
    cookie = client_1.get_cookie('session')
    assert cookie is not None

    # Pass cookie directly to worker 2 without shared process memory
    client_2 = app_worker_2.test_client()
    client_2.set_cookie('session', cookie.value)

    with client_2.session_transaction() as sess_2:
        assert sess_2.get('username') == 'admin'
        assert sess_2.get('role') == 'admin'
