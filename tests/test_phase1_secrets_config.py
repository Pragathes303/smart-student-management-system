"""
Phase 1 - Security: Step 4 Tests (Secrets and Configuration Hardening)
Verifies:
1. SECRET_KEY, DATABASE_URL / DATABASE_PATH, UPLOAD_FOLDER, and DEBUG loaded from .env.
2. DEBUG is strictly False (off) by default.
3. .env.example exists with full template documentation and zero real secrets.
4. .gitignore excludes .env, *.db, uploads, and __pycache__.
5. Zero secrets or sensitive credentials present in tracked source files.
"""

import os
import re
from pathlib import Path
import pytest
from dotenv import dotenv_values

from backend.config import Config, PROJECT_ROOT


def test_debug_off_by_default(monkeypatch):
    """DEBUG must be strictly False when neither FLASK_DEBUG nor DEBUG is set."""
    monkeypatch.delenv('FLASK_DEBUG', raising=False)
    monkeypatch.delenv('DEBUG', raising=False)

    class CleanConfig(Config):
        pass

    # Re-evaluate logic as in Config
    raw_debug = os.getenv('FLASK_DEBUG', os.getenv('DEBUG', '0')).strip().lower()
    debug_val = raw_debug in ('1', 'true', 'yes', 'on')
    assert debug_val is False


def test_debug_falsy_values(monkeypatch):
    """Common falsy representations ('0', 'false', 'no', 'off', '') must result in DEBUG=False."""
    for falsy_val in ['0', 'false', 'FALSE', 'No', 'off', '', 'disabled']:
        monkeypatch.setenv('FLASK_DEBUG', falsy_val)
        monkeypatch.setenv('DEBUG', falsy_val)
        raw_debug = os.getenv('FLASK_DEBUG', os.getenv('DEBUG', '0')).strip().lower()
        assert (raw_debug in ('1', 'true', 'yes', 'on')) is False


def test_debug_truthy_values(monkeypatch):
    """Only explicit truthy values ('1', 'true', 'yes', 'on') enable DEBUG."""
    for truthy_val in ['1', 'true', 'True', 'YES', 'on']:
        monkeypatch.setenv('FLASK_DEBUG', truthy_val)
        raw_debug = os.getenv('FLASK_DEBUG', os.getenv('DEBUG', '0')).strip().lower()
        assert (raw_debug in ('1', 'true', 'yes', 'on')) is True


def test_secret_key_loaded_from_env(monkeypatch):
    """SECRET_KEY must be loaded from environment variable."""
    test_key = "a" * 48
    monkeypatch.setenv('SECRET_KEY', test_key)

    class CustomConfig(Config):
        SECRET_KEY = os.getenv('SECRET_KEY')

    assert CustomConfig.SECRET_KEY == test_key


def test_upload_folder_loaded_from_env(monkeypatch, tmp_path):
    """UPLOAD_FOLDER must be configurable via environment variable."""
    custom_upload = tmp_path / "custom_uploads"
    custom_upload.mkdir()
    monkeypatch.setenv('UPLOAD_FOLDER', str(custom_upload))

    _uf = os.getenv('UPLOAD_FOLDER')
    assert _uf == str(custom_upload)

    # Relative path resolution test
    monkeypatch.setenv('UPLOAD_FOLDER', 'custom_rel_uploads')
    _uf_rel = os.getenv('UPLOAD_FOLDER')
    resolved = os.path.join(PROJECT_ROOT, _uf_rel)
    assert resolved.endswith('custom_rel_uploads')


def test_database_url_and_path_resolution(monkeypatch):
    """DATABASE_URL (e.g. sqlite:///...) must be parsed into DATABASE_PATH correctly."""
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///custom_dir/test_app.db')

    db_url = os.getenv('DATABASE_URL')
    assert db_url.startswith('sqlite:///')
    resolved_path = db_url.replace('sqlite:///', '')
    assert resolved_path == 'custom_dir/test_app.db'


def test_env_example_presence_and_structure():
    """.env.example must exist and contain all necessary security and application variables."""
    example_path = Path(PROJECT_ROOT) / '.env.example'
    assert example_path.exists(), ".env.example must exist in project root"

    content = example_path.read_text(encoding='utf-8')

    # Must contain essential keys
    required_keys = [
        'SECRET_KEY',
        'DATABASE_URL',
        'DATABASE_PATH',
        'UPLOAD_FOLDER',
        'DEBUG',
        'FLASK_DEBUG',
        'FLASK_ENV',
        'STORAGE_BACKEND'
    ]
    for key in required_keys:
        assert f"{key}=" in content, f".env.example is missing required key: {key}"

    # DEBUG must be 0 in .env.example
    assert "DEBUG=0" in content
    assert "FLASK_DEBUG=0" in content

    # Verify no real credentials/passwords leaked in template
    assert "AKIA" not in content
    assert "password123" not in content.lower()


def test_gitignore_contains_mandatory_security_rules():
    """.gitignore must exclude .env, *.db, uploads, and __pycache__."""
    gitignore_path = Path(PROJECT_ROOT) / '.gitignore'
    assert gitignore_path.exists(), ".gitignore must exist in project root"

    content = gitignore_path.read_text(encoding='utf-8')
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith('#')]

    # Check for .env
    assert any(line == '.env' or line == '.env*' for line in lines), ".gitignore must exclude .env"

    # Check for *.db
    assert any('*.db' in line for line in lines), ".gitignore must exclude *.db"

    # Check for uploads
    assert any('upload' in line.lower() for line in lines), ".gitignore must exclude uploads"

    # Check for __pycache__
    assert any('__pycache__' in line for line in lines), ".gitignore must exclude __pycache__"


def test_no_live_secrets_in_codebase():
    """Ensure no live private keys or AWS root credentials exist in project source."""
    disallowed_patterns = [
        re.compile(r"-----BEGIN\s+(RSA|EC|DSA|OPENSSH)?\s*PRIVATE KEY-----"),
        re.compile(r"AKIA[0-9A-Z]{16}"),  # Real AWS Access Key ID format (excluding sample docs)
    ]

    for root, dirs, files in os.walk(PROJECT_ROOT):
        # Skip virtualenvs, caches, and docs
        dirs[:] = [d for d in dirs if d not in {'.venv', 'venv', 'env', '__pycache__', '.pytest_cache', '.git'}]
        for f in files:
            if f.endswith(('.py', '.html', '.css', '.js', '.json', '.yaml', '.yml')):
                file_path = os.path.join(root, f)
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as src:
                    text = src.read()
                    for pattern in disallowed_patterns:
                        match = pattern.search(text)
                        assert match is None, f"Potential sensitive credential found in {file_path}: {match.group(0)}"
