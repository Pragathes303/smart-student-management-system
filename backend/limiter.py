"""
Rate Limiting & Brute-Force Attack Prevention Module
Powered by Flask-Limiter with Proxy-Aware IP resolution and
account lockout for repetitive failed login attempts.
"""

import time
from collections import defaultdict

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Global Flask-Limiter instance initialized without app (factory pattern)
# Configured dynamically via app.config['RATELIMIT_STORAGE_URI'] in init_app(app)
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[]
)

# In-memory tracking for failed authentication attempts: key -> list of failure timestamps
# key format: f"{ip}:{username}"
_failed_login_attempts = defaultdict(list)
MAX_LOGIN_FAILURES = 5
LOCKOUT_DURATION_SECONDS = 900  # 15 minutes


def record_failed_login(ip: str, username: str):
    """Record a failed login attempt for the given IP and username."""
    now = time.time()
    key = f"{ip}:{username.strip().lower()}"
    # Purge old attempts outside lockout window
    cutoff = now - LOCKOUT_DURATION_SECONDS
    _failed_login_attempts[key] = [t for t in _failed_login_attempts[key] if t > cutoff]
    _failed_login_attempts[key].append(now)


def clear_failed_logins(ip: str, username: str):
    """Clear failed login attempts upon successful authentication."""
    key = f"{ip}:{username.strip().lower()}"
    _failed_login_attempts.pop(key, None)


def is_login_locked(ip: str, username: str) -> tuple[bool, int]:
    """
    Check if an account or IP is temporarily locked out due to repeated failures.
    Returns: (is_locked: bool, retry_after_seconds: int)
    """
    now = time.time()
    key = f"{ip}:{username.strip().lower()}"
    cutoff = now - LOCKOUT_DURATION_SECONDS
    valid_attempts = [t for t in _failed_login_attempts[key] if t > cutoff]
    _failed_login_attempts[key] = valid_attempts

    if len(valid_attempts) >= MAX_LOGIN_FAILURES:
        oldest = valid_attempts[0]
        retry_after = max(1, int(LOCKOUT_DURATION_SECONDS - (now - oldest)))
        return True, retry_after

    return False, 0
