# Changelog - Smart Student Management System

All notable changes, security hardenings, architectural remediations, and newly added features to the Smart Student Management System are documented in this file.

---

## [2.0.0] - 2026-10-25 - Comprehensive Hardening & Feature Completion

### 🛡️ Critical Security Vulnerability Fixes (S1 – S12)
- **S1 (Broken Access Control & IDOR)**: Implemented strict Role-Based Access Control (`@admin_required`, `@staff_required`, `@login_required`) across all routes and API endpoints. Enforced student self-isolation so students can only view/edit their own profile and marks.
- **S2 (API Auth & Token Bypass)**: Secured `/api/v1/*` with dual authentication support: Bearer token verification or authenticated user session. Eliminated unauthorized JSON data leakage.
- **S3 (CSRF Protection)**: Enforced CSRF tokens on all mutating POST/PUT/DELETE forms via Flask-WTF `CSRFProtect`. Added double-submit prevention in JavaScript.
- **S4 (Insecure Verification & Reset Tokens)**: Replaced sequential/predictable IDs with cryptographically secure, high-entropy tokens stored in `students.verify_token` (perpetual, without expiration) for marksheet verification, and signed, time-limited tokens via `URLSafeTimedSerializer` for password reset workflows.
- **S5 (CSV Injection / Formula Injection)**: Implemented `sanitize_csv_cell` across all student and audit log CSV export endpoints, prefixing `=`, `+`, `-`, `@`, `\t`, `\r` with single-quote `'` and stripping null bytes.
- **S6 (Weak Password Policy)**: Enforced strong password policy: minimum 8 characters, uppercase letter, lowercase letter, number, and special character.
- **S7 (Open Redirect)**: Implemented `is_safe_redirect` to validate `next` query parameters against host boundaries and scheme validation, preventing open redirect phishing.
- **S8 (Missing Security Headers)**: Added production HTTP security headers to all responses: Content-Security-Policy (CSP), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `X-XSS-Protection: 1; mode=block`, `Referrer-Policy: strict-origin-when-cross-origin`.
- **S9 (Session Invalidation on Password Change / Logout)**: Added `session_version` integer column to `users` table. Incremented on password change and profile updates; validated on every request to instantly terminate stale sessions across all devices.
- **S10 (Brute Force Protection & Rate Limiting)**: Integrated `Flask-Limiter` on `/login`, `/forgot-password`, `/reset-password`, and API endpoints with IP-based rate limits and 15-minute progressive lockouts.
- **S11 (Insecure File Upload / RCE Mitigation)**: Added Pillow raster validation (`validate_and_sanitize_image`), format restriction (JPEG/PNG/WEBP), EXIF stripping, and UUID file re-naming.
- **S12 (SQL Injection Remediation)**: Replaced all dynamic SQL strings with parameterized queries using `?` placeholders across all filters, search terms, and updates.

### 📐 Design & Data Integrity Fixes (B1 – B12)
- **B1 (Centralized Anna University Grading)**: Built centralized `backend/grading.py` implementing the official Anna University 10-point scale (`O`, `A+`, `A`, `B+`, `B`, `RA`), with accurate credit-weighted GPA/CGPA computation and arrears detection.
- **B2 (Department Whitelist Enforcement)**: Created `departments` database table with active validation in `StudentService` and `utils.py`, preventing freeform invalid department strings.
- **B3 (Database Schema Migrations Engine)**: Built lightweight migration engine (`schema_migrations` table) in `database/init_db.py` tracking versioned upgrades without data loss.
- **B4 (Consistent Soft-Delete & Trash Management)**: Implemented soft-delete for students using `students.deleted_at` timestamp with dedicated `/students/trash`, `/students/<id>/restore`, and role-restricted permanent hard deletion, while subjects are directly hard-deleted.
- **B5 (Unique Register Number Constraints)**: Enforced unique SQLite constraints on `students.register_number` with user-friendly validation error feedback.
- **B6 (Transactional Academic Updates)**: Wrapped multi-subject grade and attendance updates in atomic SQLite transactions with automatic rollback on error.
- **B7 (N+1 Query Elimination)**: Refactored student listing and API endpoints with optimized `LEFT JOIN` aggregations for attendance summaries.
- **B8 (Dashboard Analytics TTLCache)**: Implemented `SimpleTTLCache` (180s default TTL via `DASHBOARD_CACHE_TTL`) for aggregate dashboard metrics with automated write invalidation upon any student mutation.
- **B9 (Shadowed Test Fixture Fix)**: Resolved shadowed test cases in `tests/test_app.py` ensuring comprehensive test suite execution.
- **B10 (Pluggable Storage Abstraction)**: Clean separation of `LocalStorageHandler` and `S3StorageHandler` behind uniform storage interface with mock-friendly unit testing.
- **B11 (Structured JSON Logging & Audit Trail)**: Implemented centralized logging with rotating file handlers and persistent `audit_logs` table tracking user ID, IP address, and operation details.
- **B12 (Asynchronous Background Tasks)**: Integrated `ThreadPoolExecutor` in `backend/async_tasks.py` for email sending and PDF generation without blocking web worker threads.

### 🚀 10 Missing Features Completed
1. **Forgot & Reset Password Workflow**: Secure signed-token password reset via `/forgot-password` and `/reset-password/<token>` with expiration and complexity checks.
2. **Academic Subject Management**: In-place editing of student subjects, credits, and marks via `/students/<id>/subjects/<sub_id>/edit`.
3. **Trash Bin & Recovery Workflow**: Dedicated `/students/trash` interface with one-click restore and permanent hard deletion for administrators.
4. **Daily Period-Wise Attendance Marking**: Attendance management via `/attendance` and `/attendance/mark` supporting Present, Absent, and On Duty (OD) statuses.
5. **Audit Log CSV Export**: Institutional audit log export via `/audit-logs/export` with formula injection sanitization.
6. **Student Self-Service Portal**: Student profile management via `/student/profile` allowing students to safely update contact details and upload profile photos.
7. **Semester Result Publishing Lock**: Administrative lock toggle via `/settings/semester-lock` to freeze grades during evaluation periods.
8. **User Account Status Toggle & Admin Reset**: Administrative control via `/users/<id>/toggle-status` (with last-admin deactivation guard) and `/users/<id>/reset-password`.
9. **Automated Database Backup CLI**: Flask CLI command `flask backup-db` creating timestamped, gzip-compressed SQLite snapshots in `backups/`.
10. **Database Seeding & Admin Creation CLI**: Flask CLI commands `flask init-db`, `flask create-admin`, and `flask seed-demo` for simplified DevOps orchestration.

### 🐳 DevOps & Deployment Hardening
- Multi-stage `Dockerfile` with unprivileged `appuser` and container `HEALTHCHECK`.
- Production `docker-compose.yml` with named persistent volumes (`db_data`, `log_data`, `upload_data`).
- Pinned production `requirements.txt` and developer `requirements-dev.txt`.
- GitHub Actions CI workflow (`.github/workflows/ci.yml`) with automated Ruff linting, Bandit security scanning, and Pytest coverage gates.
