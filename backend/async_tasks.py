"""
Background Async Task Executor
Powered by concurrent.futures.ThreadPoolExecutor (2-4 workers)
Enables asynchronous email dispatch, heavy reporting, and audit logging
without blocking HTTP request threads (B8).
"""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from flask import current_app

logger = logging.getLogger(__name__)

# Fixed worker pool (3 workers for balanced concurrency without thread exhaustion)
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="async-worker")


def run_in_background(task_func: Callable, *args, **kwargs):
    """
    Submit a task to run asynchronously in the background worker pool
    with the current Flask application context preserved.
    """
    app = current_app._get_current_object() if current_app else None

    def _wrapped_task():
        if app:
            with app.app_context():
                try:
                    return task_func(*args, **kwargs)
                except Exception as e:
                    app.logger.error("Async task '%s' failed: %s", task_func.__name__, str(e), exc_info=True)
        else:
            try:
                return task_func(*args, **kwargs)
            except Exception as e:
                logger.error("Async task '%s' failed: %s", task_func.__name__, str(e), exc_info=True)

    _executor.submit(_wrapped_task)
