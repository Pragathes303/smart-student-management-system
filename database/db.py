"""
Database Connection, Query Execution and Transaction Module
Provides safe, parameterized SQLite database interactions with WAL journal mode,
busy timeout handling, foreign key enforcement, and atomic transaction context management.
"""

import os
import sqlite3
from contextlib import contextmanager

from flask import current_app, g

DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), 'students.db')


def normalize_database_url(url: str | None) -> str | None:
    """Normalize legacy postgres:// prefix (Heroku/Render) to postgresql://."""
    if not url:
        return None
    url = url.strip()
    if url.startswith('postgres://'):
        return url.replace('postgres://', 'postgresql://', 1)
    return url


def get_database_url() -> str | None:
    """Retrieve database connection URL if defined in current app config or environment."""
    raw_url = None
    try:
        if current_app:
            raw_url = current_app.config.get('DATABASE_URL')
    except RuntimeError:
        pass
    if not raw_url:
        raw_url = os.getenv('DATABASE_URL')
    return normalize_database_url(raw_url)


def is_postgres() -> bool:
    """Check if current active configuration targets a PostgreSQL database."""
    url = get_database_url()
    return bool(url and (url.startswith('postgresql://') or url.startswith('postgresql+')))


def _is_pg_connection(conn) -> bool:
    """Determine if a connection instance is a PostgreSQL connection."""
    module_name = getattr(type(conn), '__module__', '')
    return 'psycopg2' in module_name or (conn is not None and is_postgres() and not isinstance(conn, sqlite3.Connection))


def _convert_placeholders(query: str) -> str:
    """Convert SQLite parameter placeholder '?' to PostgreSQL '%s'."""
    return query.replace('?', '%s')


def get_db_path():
    """Retrieve database path from current app config or fallback to default."""
    try:
        if current_app:
            return current_app.config.get('DATABASE_PATH', DEFAULT_DB_PATH)
    except RuntimeError:
        pass
    db_url = get_database_url()
    if db_url and db_url.startswith('sqlite:///'):
        return db_url.replace('sqlite:///', '')
    return os.getenv('DATABASE_PATH', DEFAULT_DB_PATH)


def _configure_connection(conn: sqlite3.Connection):
    """Apply performance, concurrency and foreign key enforcement PRAGMAs."""
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")


def get_db(custom_path=None):
    """
    Get a database connection (PostgreSQL when DATABASE_URL is postgresql://, else SQLite).
    If running inside a Flask application context, cache it in `g.db`.
    """
    # Connect to PostgreSQL if configured and no custom SQLite file path was explicitly provided
    if is_postgres() and not custom_path:
        dsn = get_database_url()
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
        except ImportError as e:
            raise RuntimeError(
                "psycopg2 is required for PostgreSQL support. Install with: pip install psycopg2-binary"
            ) from e

        try:
            if 'db' not in g:
                conn = psycopg2.connect(dsn, cursor_factory=RealDictCursor)
                g.db = conn
            return g.db
        except RuntimeError:
            return psycopg2.connect(dsn, cursor_factory=RealDictCursor)

    # SQLite connection
    path = custom_path or get_db_path()

    # Ensure parent directory exists
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    # Use Flask request context `g` if available
    try:
        if 'db' not in g:
            conn = sqlite3.connect(path)
            _configure_connection(conn)
            g.db = conn
        return g.db
    except RuntimeError:
        # Outside Flask application context (e.g. init script or CLI)
        conn = sqlite3.connect(path)
        _configure_connection(conn)
        return conn


def close_db(e=None):
    """Close the database connection if open in Flask request context."""
    try:
        db = g.pop('db', None)
        if db is not None:
            db.close()
    except RuntimeError:
        pass


@contextmanager
def transaction(custom_db=None):
    """
    Context manager for atomic database transactions.
    Automatically commits on success or rolls back on exception.
    """
    db = custom_db if custom_db is not None else get_db()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise


def query_db(query, args=(), one=False, custom_db=None):
    """
    Execute a parameterized SELECT query across SQLite or PostgreSQL.
    
    Security: Strictly uses parameterized placeholders to guarantee immunity
    from SQL Injection attacks.
    
    Args:
        query (str): SQL query string with '?' placeholders.
        args (tuple/list): Parameters to bind to the placeholders.
        one (bool): If True, returns a single row dictionary or None.
        custom_db: Optional existing connection (for transactions or CLI scripts).
        
    Returns:
        list of dicts or single dict (or None if one=True and no result).
    """
    db = custom_db if custom_db is not None else get_db()
    if _is_pg_connection(db):
        from psycopg2.extras import RealDictCursor
        cursor = db.cursor(cursor_factory=RealDictCursor)
        pg_query = _convert_placeholders(query)
        cursor.execute(pg_query, args)
        rows = cursor.fetchall()
        cursor.close()
        result = [dict(row) for row in rows]
        if one:
            return result[0] if result else None
        return result

    cursor = db.cursor()
    cursor.execute(query, args)
    rows = cursor.fetchall()
    cursor.close()
    
    result = [dict(row) for row in rows]
    if one:
        return result[0] if result else None
    return result


def execute_db(query, args=(), commit=True, custom_db=None):
    """
    Execute a parameterized INSERT, UPDATE, or DELETE query across SQLite or PostgreSQL.
    
    Security: Strictly uses parameterized queries.
    
    Args:
        query (str): SQL query string with '?' placeholders.
        args (tuple/list): Parameters to bind.
        commit (bool): Whether to commit immediately.
        custom_db: Optional existing connection.
        
    Returns:
        dict: {'lastrowid': int | None, 'rowcount': int}
    """
    db = custom_db if custom_db is not None else get_db()
    if _is_pg_connection(db):
        from psycopg2.extras import RealDictCursor
        cursor = db.cursor(cursor_factory=RealDictCursor)
        pg_query = _convert_placeholders(query)
        q_strip = pg_query.strip().rstrip(';')
        has_returning = 'RETURNING' in q_strip.upper()
        # Automatically append RETURNING id to capture lastrowid for PostgreSQL INSERTs
        if q_strip.upper().startswith('INSERT INTO') and not has_returning:
            pg_query = f"{q_strip} RETURNING id"

        cursor.execute(pg_query, args)
        if commit:
            db.commit()

        lastrowid = None
        if 'RETURNING' in pg_query.upper():
            try:
                row = cursor.fetchone()
                if row:
                    lastrowid = row.get('id', next(iter(row.values()), None)) if isinstance(row, dict) else row[0]
            except Exception:
                pass

        rowcount = cursor.rowcount
        cursor.close()
        return {'lastrowid': lastrowid, 'rowcount': rowcount}

    cursor = db.cursor()
    cursor.execute(query, args)
    lastrowid = cursor.lastrowid
    # Consume active cursor before commit if RETURNING is present (prevents SQLite 'statements in progress' error)
    if 'RETURNING' in query.upper():
        try:
            row = cursor.fetchone()
            if row:
                lastrowid = row[0]
        except Exception:
            pass
    if commit:
        db.commit()
    rowcount = cursor.rowcount
    cursor.close()
    return {'lastrowid': lastrowid, 'rowcount': rowcount}


def init_db(db_path=None):
    """
    Initialize the database using the schema.sql file.
    Creates tables and indices if they do not exist.
    Supports both SQLite and PostgreSQL.
    """
    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')

    if is_postgres() and not db_path:
        conn = get_db()
        with open(schema_path, 'r', encoding='utf-8') as f:
            raw_sql = f.read()

        # Adapt SQLite schema for PostgreSQL
        # 1. Strip SQLite PRAGMA commands
        pg_lines = [line for line in raw_sql.splitlines() if not line.strip().startswith('PRAGMA')]
        pg_sql = "\n".join(pg_lines)
        # 2. Convert AUTOINCREMENT to SERIAL
        pg_sql = pg_sql.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'SERIAL PRIMARY KEY')
        # 3. Handle circular dependency: users -> students -> users
        # users has FOREIGN KEY (student_id) REFERENCES students(id) before students table exists
        pg_sql = pg_sql.replace(
            "FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE",
            "-- fk_users_student deferred"
        )
        # Add foreign key constraint after all tables exist
        pg_sql += """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_users_student') THEN
                ALTER TABLE users ADD CONSTRAINT fk_users_student FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE;
            END IF;
        END $$;
        """

        cursor = conn.cursor()
        cursor.execute(pg_sql)
        conn.commit()
        cursor.close()
        return True

    path = db_path or get_db_path()
    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
    
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    _configure_connection(conn)

    # Safely migrate pre-existing tables before executing schema indices
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if 'students' in tables:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(students)").fetchall()]
            if 'verify_token' not in cols:
                conn.execute("ALTER TABLE students ADD COLUMN verify_token TEXT DEFAULT NULL;")
            if 'deleted_at' not in cols:
                conn.execute("ALTER TABLE students ADD COLUMN deleted_at TIMESTAMP DEFAULT NULL;")
            if 'cgpa' not in cols:
                conn.execute("ALTER TABLE students ADD COLUMN cgpa REAL DEFAULT NULL;")
            if 'year' not in cols:
                conn.execute("ALTER TABLE students ADD COLUMN year INTEGER NOT NULL DEFAULT 1;")
            conn.commit()
        if 'audit_logs' in tables:
            audit_cols = [r[1] for r in conn.execute("PRAGMA table_info(audit_logs)").fetchall()]
            if 'old_value' not in audit_cols:
                conn.execute("ALTER TABLE audit_logs ADD COLUMN old_value TEXT DEFAULT NULL;")
            if 'new_value' not in audit_cols:
                conn.execute("ALTER TABLE audit_logs ADD COLUMN new_value TEXT DEFAULT NULL;")
            conn.commit()
        if 'users' in tables:
            user_cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
            if 'department' not in user_cols:
                conn.execute("ALTER TABLE users ADD COLUMN department TEXT DEFAULT NULL;")
            if 'assigned_subjects' not in user_cols:
                conn.execute("ALTER TABLE users ADD COLUMN assigned_subjects TEXT DEFAULT NULL;")
            conn.commit()
        if 'attendance_records' in tables:
            att_cols = [r[1] for r in conn.execute("PRAGMA table_info(attendance_records)").fetchall()]
            if 'subject_code' not in att_cols:
                conn.execute("ALTER TABLE attendance_records ADD COLUMN subject_code TEXT DEFAULT 'GENERAL';")
            conn.commit()
    except Exception:
        pass


    with open(schema_path, 'r', encoding='utf-8') as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
    return True
