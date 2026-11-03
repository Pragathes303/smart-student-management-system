# Smart Student Management System (Flask + PostgreSQL / SQLite + Cloud Ready)

A professional, enterprise-grade **Smart Student Management System** built with **Python 3.12**, **Flask**, **PostgreSQL** / **SQLite**, **HTML**, and **Vanilla CSS**. Designed with clean layered architecture, Anna University 10-point academic grading, 3-tier Role-Based Access Control (Admin, Staff, Student), persistent cloud database support (Neon, Supabase, Render), and pluggable cloud storage ready for **AWS EC2**, **Amazon S3**, and **Docker** containerized deployment.

---

## 🌟 Key Features

1. **Student Records Management (Full CRUD + Soft-Delete)**:
   - Add new students with photo upload, register number, department, email, and marks.
   - View individual student academic scorecards with calculated letter grades and CGPA.
   - In-place editing of student academic details and subjects.
   - Soft-delete with **Trash Bin** (`/students/trash`), restore, and role-restricted permanent hard deletion.
2. **Centralized Anna University 10-Point Academic Grading**:
   - Standard Anna University scale (`O`, `A+`, `A`, `B+`, `B`, `RA`).
   - Credit-weighted Semester GPA and Cumulative CGPA computation.
   - Automatic arrears detection and academic standing categorization.
3. **Official PDF Marksheet / Grade Card Download**:
   - One-click institutional PDF report card download using `ReportLab` with verified barcode, grade scales, and examination seal.
4. **Daily Period-Wise Attendance Tracking**:
   - Period-wise daily attendance marking (`/attendance/mark`) supporting Present (P), Absent (A), and On Duty (OD).
   - Real-time attendance percentage aggregation.
5. **Dynamic Academic Dashboard & Visual Charts**:
   - Real-time KPIs: Total Enrolled Students, Average Marks, Highest Score, and Passing Percentage.
   - Fast sub-millisecond cached responses via `SimpleTTLCache` (60s TTL) with automated write-invalidation.
   - Department-wise breakdown with progress meters and percentage distributions.
   - Interactive visual charts (Department Donut Chart and Institutional Grade Breakdown Bar Chart).
   - Top academic performers leaderboard and recent enrollments roster.
6. **Smart Search, Multi-Criteria Filtering & Pagination**:
   - Instant search across Student Name, Register Number, and Email.
   - Filter by Department and Academic Performance (Distinction $\ge 80$, Passing $\ge 50$, At Risk $< 50$).
   - Multi-field sorting (Name A-Z/Z-A, Marks High-Low/Low-High, Register No, Date).
   - Clean table pagination preserving active filters.
7. **Institutional Security & RBAC (Role-Based Access Control)**:
   - Three distinct roles: **Admin**, **Staff**, and **Student**.
   - Admin accounts can manage users, view system audit trails, restore/hard-delete records, and lock semester grades.
   - Staff accounts can view, add, and edit student profiles and mark attendance.
   - Student self-service portal (`/student/profile`) for updating personal contact information and profile photos.
   - Password reset workflow with signed expiration tokens (`/forgot-password`, `/reset-password/<token>`).
   - High-entropy 24-character email verification tokens.
   - Instant session termination on password changes via `session_version` validation.
   - Brute force protection with `Flask-Limiter` rate limiting and 15-minute lockouts.
   - Production HTTP security headers: CSP, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`.
8. **Institutional Security Audit Trail**:
   - Automatic logging of user logins, password changes, student modifications, imports, and deletions to both rotating log files and a dedicated SQLite `audit_logs` table.
   - Sanitized CSV audit log export (`/audit-logs/export`) with formula injection defense.
9. **Persistent Cloud Database Engine (PostgreSQL & SQLite Dual Dialect)**:
   - Full support for cloud PostgreSQL via `DATABASE_URL` (compatible with Neon, Supabase, Render, AWS RDS).
   - Dynamic parameter translation (`?` to `%s`), automatic legacy `postgres://` to `postgresql://` conversion, and cross-engine `RETURNING id` lastrowid extraction.
   - Zero-config local development with SQLite (`PRAGMA foreign_keys = ON`, WAL mode).
   - Fully idempotent migrations and admin onboarding preventing data loss or password resets across deploys.
   - Comprehensive guide in [docs/postgres_setup.md](file:///c:/cloud%20project/docs/postgres_setup.md).
10. **Pluggable Cloud Storage Abstraction (AWS S3 & Local)**:
    - File uploads route through `backend/storage.py` with strict raster verification and EXIF scrubbing via Pillow.
    - Default: `LocalStorageHandler` saves files locally to `frontend/static/uploads/`.
    - Cloud: Switch `STORAGE_BACKEND=s3` in `.env` to route uploads directly to Amazon S3 without touching application code.
11. **DevOps & Production CLI Management Tools**:
    - Multi-stage Docker containerization with non-root security execution.
    - Standardized Python 3.12 runtime and WSGI entrypoint (`app:app`).
    - Flask CLI management commands for database backups, administrative onboarding, and test seeding.

---

## 🔑 User Authentication & Role Provisioning

The system implements Role-Based Access Control (RBAC) across three distinct roles:
- **Administrator**: Full administrative access (Users, Audits, Semester Lock, Trash Bin, CRUD).
- **Academic Staff**: Academic operational access (View, Add, Edit Students, Mark Attendance).
- **Student (Self-Service)**: Student portal (Personal Scorecard, Profile & Photo updates).

Initial administrator accounts are securely provisioned via environment variables (`ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ADMIN_EMAIL`) in `.env` or interactively via the CLI command:
```bash
flask create-admin
```
No default passwords are hardcoded in the codebase.

---

## 🎓 Anna University 10-Point Grade Scale

| Marks Range | Letter Grade | Grade Point | Performance Description | Result |
| :---: | :---: | :---: | :--- | :---: |
| 90 – 100 | **O** | 10.0 | Outstanding | Pass |
| 80 – 89 | **A+** | 9.0 | Excellent | Pass |
| 70 – 79 | **A** | 8.0 | Very Good | Pass |
| 60 – 69 | **B+** | 7.0 | Good | Pass |
| 50 – 59 | **B** | 6.0 | Average | Pass |
| 0 – 49 | **RA** | 0.0 | Reappear (Arrear) | Fail |

---

## 🚀 Quick Start Guide

### 1. Prerequisites
- Python 3.12 (Standardized production & development runtime)
- SQLite3 (zero-config local default) or PostgreSQL 15+ (for cloud persistence via `DATABASE_URL`)
- Virtualenv (`python -m venv venv`)

### 2. Setup Environment
```bash
# Clone and enter workspace
cd "c:\cloud project"

# Create and activate virtual environment
python -m venv venv
venv\Scripts\activate   # Windows
# source venv/bin/activate # Linux/macOS

# Install dependencies
pip install -r requirements.txt
```

### 3. Initialize Database & Seed Demo Data
```bash
# Initialize schema and seed default users & student records
python database/init_db.py
```

### 4. Run Application Server
```bash
python run.py
```
Access the application at 👉 **`http://127.0.0.1:5000`**

---

## 🛠️ Flask CLI Commands

The system provides streamlined CLI commands for operations and deployment:

```bash
# Run database schema migrations and seed initial accounts
flask init-db

# Create an administrator account interactively
flask create-admin

# Create a timestamped, gzip-compressed database snapshot in backups/
flask backup-db

# Seed 20+ realistic student records for load testing
flask seed-demo
```

---

## 🐳 Docker Deployment

Run the system in an isolated, multi-stage production container:

```bash
# Build and start services using Docker Compose
docker-compose up -d --build

# View container logs
docker-compose logs -f app

# Stop services
docker-compose down
```

---

## 🧪 Automated Testing & Quality Gates

The project contains a comprehensive test suite covering all modules:

```bash
# Run all tests
pytest -v

# Run with test coverage
pytest --cov=backend --cov-report=term-missing

# Run Ruff code linter
ruff check backend

# Run Bandit security scanner
bandit -r backend -ll
```

---

## 🔒 Security Architecture Highlights

1. **SQL Injection Defense**: 100% parameterized SQLite queries with `?` bindings across all queries.
2. **Formula Injection Defense**: All CSV exports sanitize cells against spreadsheet command execution (`=`, `+`, `-`, `@`).
3. **Session Hardening**: Secure cookie flags (`HttpOnly`, `SameSite=Lax`), `session_version` invalidation on logout and password change.
4. **Pillow Raster Sanitization**: Strict image inspection, EXIF stripping, and UUID re-naming against malicious upload payloads.
5. **Rate Limiting**: Multi-tier rate limiting on sensitive endpoints preventing credential stuffing and brute force attempts.
6. **Production Security Headers**: Strict CSP, clickjacking prevention (`X-Frame-Options: DENY`), and MIME-sniffing prevention (`nosniff`).

---

## ☁️ Free Cloud Deployment Guide

### Option 1: Render.com (Recommended - Automated CI/CD & Free HTTPS)
1. **Push to GitHub**: Commit code to a GitHub repository (`smart-student-system`).
2. **Create Web Service on Render**:
   - Go to [render.com](https://render.com) &rarr; **New +** &rarr; **Web Service**.
   - Connect your GitHub repository.
   - **Environment:** `Python` (3.12.8)
   - **Build Command:**
     ```bash
     pip install --upgrade pip && pip install -r requirements.txt && python database/init_db.py
     ```
   - **Start Command:**
     ```bash
     gunicorn --workers 2 --timeout 90 --bind 0.0.0.0:$PORT app:app
     ```
   - **Environment Variables**:
     - `FLASK_ENV`: `production`
     - `FLASK_DEBUG`: `0`
     - `SECRET_KEY`: *(Generate a secure 64-char key using `python -c "import secrets; print(secrets.token_hex(32))"`)*
     - `DATABASE_URL`: `postgresql://user:pass@host/dbname?sslmode=require` *(Provision a free Neon or Supabase DB; see [docs/postgres_setup.md](docs/postgres_setup.md) to prevent data loss on Render's ephemeral filesystem)*
     - `STORAGE_BACKEND`: `local` *(or `s3` with AWS credentials)*
     - `ADMIN_USERNAME`: `admin`
     - `ADMIN_PASSWORD`: `<set-a-strong-password>`
     - `ADMIN_EMAIL`: `admin@institution.edu`
3. **Verify Health**: Check `https://your-service.onrender.com/health` &rarr; Returns `{"status": "healthy"}`.

### Option 2: PythonAnywhere (Direct ZIP Upload - No Git Required)
1. Sign up for a free Beginner account at [pythonanywhere.com](https://www.pythonanywhere.com).
2. Go to the **Files** tab and upload `student_management_system.zip`.
3. Open a **Bash Console** and run:
   ```bash
   unzip student_management_system.zip
   pip install --user -r requirements.txt
   python3 database/init_db.py
   ```
4. In the **Web** tab &rarr; **Add a new web app** &rarr; Manual configuration (Python 3.10/3.11).
5. Edit your WSGI configuration file:
   ```python
   import sys, os
   project_home = '/home/yourusername'
   if project_home not in sys.path:
       sys.path.insert(0, project_home)
   from backend.app import create_app
   application = create_app()
   ```
6. Click **Reload <yourusername>.pythonanywhere.com**.

---

## ✅ Production Deployment Checklist

Before taking the system live for production or examiner evaluation, complete and verify each of the following 8 critical items:

- [ ] **1. Secret Key Set**: `SECRET_KEY` is configured via environment variables to a high-entropy, cryptographically secure random string (minimum 32 characters, e.g. generated via `python -c "import secrets; print(secrets.token_hex(32))"`). Never rely on development default secrets in production.
- [ ] **2. Debug Off**: Production mode is strictly enforced with `FLASK_DEBUG=0` and `FLASK_ENV=production`. Werkzeug interactive debuggers and traceback exposures are completely disabled.
- [ ] **3. Default Passwords Changed**: Any initial bootstrap passwords (such as `<set-a-strong-password>`) must be changed immediately upon first login. Administrator credentials must satisfy high complexity standards (uppercase, lowercase, numbers, and symbols).
- [ ] **4. HTTPS On**: SSL/TLS certificate is active and enforced. All HTTP requests redirect to HTTPS, and secure cookie flags (`__Host-session`, `HttpOnly`, `SameSite=Lax`, `Secure`) are active.
- [ ] **5. Backup Tested**: Database backup workflow is validated by executing `python database/backup_db.py` (or `flask backup-db`) and confirming snapshot creation and integrity verification (`flask verify-backup`).
- [ ] **6. Rate Limiting On**: `Flask-Limiter` is active and operational on `/login`, `/register`, `/forgot-password`, `/attendance/mark`, and bulk imports to mitigate brute-force and DDoS attacks.
- [ ] **7. .env Not in Git**: Sensitive `.env` files are verified to be absent from source control commits (`.gitignore` excludes `.env`, `*.db`, `backups/`). Verified via `git status` and `git check-ignore -v .env`.
- [ ] **8. Admin Accounts Reviewed**: User directory (`/users`) is audited so that only authorized administrative staff possess the `admin` role, and test accounts are deactivated or role-restricted.

> 📖 **Comprehensive Deployment Guides**:
> - [Zero-Cost Cloud Deployment Guide (Render & PythonAnywhere)](file:///c:/cloud%20project/cloud/free_hosting_guide.md)
> - [Automated Live URL Security Verifier](file:///c:/cloud%20project/cloud/verify_production_deployment.py)
> - [AWS EC2 & Nginx Deployment Guide](file:///c:/cloud%20project/cloud/aws_ec2_deployment_guide.md)

---

## ⚡ Phase 6: Performance & Multi-Worker Readiness

- **PostgreSQL & SQLite Dual Engine**: Native PostgreSQL connection support via `DATABASE_URL` with automatic `postgres://` to `postgresql://` prefix modernization. SQLite remains zero-config default for local development. Parameter placeholder conversion (`?` to `%s`) and atomic `RETURNING id` extraction work across both backends.
- **N+1 Query Elimination & Eager Loading**: Multi-table relationships (`student_attendance`, `student_subjects`) are eagerly loaded using single `LEFT JOIN` queries with strict `LIMIT` and `OFFSET` pagination across students, archived trash, user management, and audit logs.
- **Analytics Caching**: In-memory thread-safe TTL cache (`SimpleTTLCache`) caches aggregate dashboard KPIs for 1–5 minutes (`DASHBOARD_CACHE_TTL=180` by default). Atomic write-invalidation automatically flushes stale metrics when records are inserted, modified, or deleted.
- **Multi-Worker Gunicorn & Stateless Architecture**: Fully stateless HTTP sessions signed via HMAC cookies allow horizontal scaling (`gunicorn --workers 4 --threads 2 app:app`). The rate limiter storage (`RATELIMIT_STORAGE_URI`) dynamically connects to Redis clusters or distributed storage without code modification.
- **Live 30-Second Dashboard Polling**: Real-time client polling (`GET /api/v1/dashboard/kpis`) updates KPIs, charts, and metrics every 30 seconds with a visual live pulse indicator.

---

## 📄 License
This project is open-source and intended for academic institution management.
