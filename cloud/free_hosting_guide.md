# 🚀 100% Free Cloud Deployment Guide & Runbook (Zero Cost, No Credit Card)
## Smart Student Management System - Phase 7 Production Deployment

This step-by-step guide explains how to deploy the application to a **Live Public HTTPS URL** using either **Render.com** (recommended container hosting) or **PythonAnywhere** (direct upload, no Git required). Both platforms offer genuine free tiers with no credit card required.

---

## 📋 Pre-Deployment Architecture & Production Settings

Before initiating deployment, verify these core configurations are set in your cloud dashboard:

| Configuration Variable | Recommended Production Value | Description |
| :--- | :--- | :--- |
| `FLASK_ENV` | `production` | Enables strict security, production session cookie security (`__Host-session`), and security headers. |
| `FLASK_DEBUG` | `0` | Disables interactive debuggers and stack trace leaks. |
| `SECRET_KEY` | *(64-char hex string)* | Cryptographic session and CSRF signature key. Generate via `python -c "import secrets; print(secrets.token_hex(32))"`. |
| `DATABASE_URL` | *(Optional PostgreSQL URL)* | Supports cloud PostgreSQL (`postgresql://...`). Defaults to SQLite if omitted. |
| `STORAGE_BACKEND` | `local` (or `s3`) | `local` for disk storage, `s3` for AWS S3 / Cloudflare R2 cloud object storage. |
| `DASHBOARD_CACHE_TTL` | `180` | Cache TTL (3 minutes) for analytics metrics. |
| `RATELIMIT_STORAGE_URI`| `memory://` (or `redis://`) | Configurable rate limiter storage backend. |
| `ADMIN_USERNAME` | `admin` | Initial administrative user name. |
| `ADMIN_PASSWORD` | *(Strong custom password)* | Must contain uppercase, lowercase, numbers, and symbols. |

---

## 🖼️ Uploaded Photos & Ephemeral Filesystem Strategy

> [!WARNING]
> **Ephemeral Filesystem Limitation on Free Cloud Hosts**:
> Containerized free hosting tiers (such as Render free tier, Heroku, or Fly.io) run on ephemeral storage. Every time your service restarts, rebuilds, or wakes up from idle sleep (after 15 minutes of inactivity), any files written to the local disk during runtime (such as `frontend/static/uploads/`) are purged.

### Three Production Solutions Provided by the Application:

1. **Automatic Initial Avatar Fallback (Built-in, Zero Config)**:
   - The application's [`LocalStorageHandler.get_url()`](file:///c:/cloud%20project/backend/storage.py) checks if uploaded photo files exist on disk before generating links.
   - If a container restart wiped the local upload folder, the application gracefully returns `None`, causing the UI to automatically render the student's color-coded initials avatar (`{{ student.name[:2]|upper }}`) instead of displaying broken 404 images.
2. **Persistent Disk Storage (`PERSISTENT_DISK_PATH`)**:
   - On hosts with attached storage (like Render Paid Disks, Docker Volumes, or PythonAnywhere), set `PERSISTENT_DISK_PATH=/var/data/uploads` or `UPLOAD_FOLDER=/var/data/uploads`.
   - On **PythonAnywhere Free Tier**, the filesystem `/home/yourusername/` is **permanently persistent by default**, so local photo uploads are never erased!
3. **Cloud Object Storage (`STORAGE_BACKEND=s3`)**:
   - Set `STORAGE_BACKEND=s3` along with `AWS_S3_BUCKET`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, and `AWS_SECRET_ACCESS_KEY`.
   - Compatible with Amazon S3, Cloudflare R2 (10 GB free), Backblaze B2, or Supabase Storage.

---

## 🌐 Method 1: Render.com Deployment (Recommended)

Render provides automated Docker/Python container builds with native HTTPS (`https://*.onrender.com`).

### Step 1: Push Code to GitHub
1. Log in to [GitHub](https://github.com/) and create a new repository named `smart-student-system` (Public or Private).
2. Push your project code to GitHub:
   ```bash
   git init
   git add .
   git commit -m "feat: complete student management system production build"
   git branch -M main
   git remote add origin https://github.com/<your-username>/smart-student-system.git
   git push -u origin main
   ```
   *(Or drag and drop files via the GitHub web interface).*

### Step 2: Create Web Service on Render
1. Open [render.com](https://render.com/) and sign in with GitHub.
2. In the Render Dashboard, click **New +** &rarr; Select **Web Service**.
3. Select **Build and deploy from a Git repository** &rarr; Connect your `smart-student-system` repository.
4. Configure service parameters:
   - **Name:** `student-portal` *(or your custom sub-domain)*
   - **Region:** `Frankfurt (EU)`, `Oregon (US West)`, or `Singapore`
   - **Branch:** `main`
   - **Runtime:** `Python 3`
   - **Build Command:**
     ```bash
     pip install --upgrade pip && pip install -r requirements.txt && python database/init_db.py
     ```
   - **Start Command:**
     ```bash
     gunicorn --workers 2 --threads 2 --timeout 90 app:app
     ```
   - **Instance Type:** Select **Free ($0/month)**.

### Step 3: Configure Environment Variables
Under the **Environment Variables** section on Render, add:

| Key | Value | Notes |
| :--- | :--- | :--- |
| `FLASK_ENV` | `production` | Enforces production mode and cookies |
| `FLASK_DEBUG` | `0` | Disables interactive debuggers |
| `SECRET_KEY` | *(Generate via `python -c "import secrets; print(secrets.token_hex(32))"`)* | Cryptographic security |
| `STORAGE_BACKEND` | `local` | Uses local storage with avatar fallback |
| `ADMIN_USERNAME` | `admin` | Initial admin username |
| `ADMIN_PASSWORD` | `<set-a-strong-password>` | Strong initial password |
| `ADMIN_EMAIL` | `admin@institution.edu` | Admin notification address |
| `DASHBOARD_CACHE_TTL` | `180` | Cache dashboard metrics for 3 mins |
| `RATELIMIT_STORAGE_URI` | `memory://` | Multi-worker rate limiter |

5. Click **Create Web Service**.

### Step 4: Monitor Build and Check Live Logs
1. Click the **Logs** tab in Render to observe real-time deployment:
   - Pip installs dependencies (`psycopg2-binary`, `reportlab`, `openpyxl`, `flask-limiter`).
   - `python database/init_db.py` executes schema migrations, creates tables, and seeds initial administrator.
   - Gunicorn starts with 2 workers and 2 threads: `[INFO] Listening at: http://0.0.0.0:10000`.
2. When the status changes to **Live**, click your public URL:
   👉 **`https://student-portal.onrender.com`**

### Step 5: Render Scheduled Backups
- Render Free Tier puts services to sleep after 15 minutes of inactivity. To trigger daily backups:
  - Use a free external cron monitor like [cron-job.org](https://cron-job.org) or GitHub Actions.
  - Or run manual backup snapshots from the Render Shell:
    ```bash
    python database/backup_db.py
    flask verify-backup
    ```

---

## 🐍 Method 2: PythonAnywhere Deployment (Direct Upload - No GitHub Needed)

PythonAnywhere provides a persistent home directory (`/home/username`) where your database and uploaded student photos are **100% persistent across server restarts**.

### Step 1: Create Account
1. Visit [pythonanywhere.com](https://www.pythonanywhere.com/) and register for a **Beginner Account** (Free forever).
2. Confirm your email address.

### Step 2: Upload Project Files
1. Create a zip archive of your project or use `student_management_system.zip`.
2. Navigate to the **Files** tab &rarr; Upload `student_management_system.zip` into `/home/<username>/`.
3. Open a **Bash Console** from the **Consoles** tab and execute:
   ```bash
   unzip student_management_system.zip
   pip install --user -r requirements.txt
   python3 database/init_db.py
   ```

### Step 3: Configure WSGI Web App
1. Go to the **Web** tab &rarr; Click **Add a new web app**.
2. Select **Manual configuration** &rarr; Choose **Python 3.10** or **Python 3.11**.
3. Under the **Code** section:
   - **Source code:** `/home/<yourusername>`
   - **Working directory:** `/home/<yourusername>`
4. Click on the **WSGI configuration file** link (`/var/www/<yourusername>_pythonanywhere_com_wsgi.py`).
5. Replace all content with:
   ```python
   import sys
   import os

   project_home = '/home/<yourusername>'
   if project_home not in sys.path:
       sys.path.insert(0, project_home)

   # Set production environment variables
   os.environ['FLASK_ENV'] = 'production'
   os.environ['FLASK_DEBUG'] = '0'
   os.environ['SECRET_KEY'] = 'your-secure-random-secret-key-32-chars-min'

   from backend.app import create_app
   application = create_app()
   ```
   *(Replace `<yourusername>` with your actual username)*.
6. Click **Save**, return to the **Web** tab, and click the green **Reload** button.
7. Visit your live site:
   👉 **`https://<yourusername>.pythonanywhere.com`**

### Step 4: PythonAnywhere Scheduled Daily Backups (Free)
1. Go to the **Tasks** tab in PythonAnywhere.
2. In the **Scheduled tasks** section, configure:
   - **Time (UTC):** `02:00`
   - **Command:** `python3 /home/<yourusername>/database/backup_db.py`
3. Click **Create**. PythonAnywhere will automatically generate daily compressed backups in `/home/<yourusername>/backups/`.

---

## 🔒 Automated Live URL Security Verification

Once your site is live, run the verification tool against your URL to validate HTTPS, TLS cookies, security headers, and rate limiting:

```bash
python cloud/verify_production_deployment.py https://student-portal.onrender.com
```

### Manual cURL Security Checks:

```bash
# 1. Verify HTTPS & HTTP-to-HTTPS Redirection
curl -I http://student-portal.onrender.com

# 2. Verify Security Headers (HSTS, CSP, X-Frame-Options, nosniff)
curl -I https://student-portal.onrender.com/login

# 3. Verify Health Probe
curl -s https://student-portal.onrender.com/health | jq .

# 4. Verify Interactive Debugger is Disabled (Custom 404 Page)
curl -s https://student-portal.onrender.com/trigger-404-test | grep -i "werkzeug"
```

---

## 🧪 Live URL End-to-End Walkthrough (Role by Role)

### 1. Test as Administrator (`admin`)
1. Navigate to `https://<your-domain>/login` &rarr; Enter username `admin` and your configured password.
2. **Dashboard**: Verify the **Live (30s)** indicator pulses green. Check total enrollment and Anna University grade distribution charts.
3. **Monitoring**: Navigate to `/admin/monitoring`. Inspect system uptime, memory usage, database ping latency, and Prometheus metrics (`/metrics`).
4. **User Management**: Navigate to `/users`. Create a new Staff account (`staff1`, department: `Computer Science`).
5. **System Controls**: Test toggling the semester marks publishing lock under Settings.
6. **Audit Trail**: Visit `/audit-logs`. Confirm all administrator operations, IP addresses, and timestamps are recorded.

### 2. Test as Academic Staff (`staff`)
1. Log in with the Staff account (`staff` or newly created `staff1`).
2. **Student Directory**: Browse `/students`. Test searching by registration number, filtering by department, and paginating records.
3. **Add Student**: Add a student with photo, department, and initial marks.
4. **Academics & GPA**: Open student details. Add semester subjects & credits; observe automatic calculation of Anna University SGPA and CGPA.
5. **Attendance Module**: Visit `/attendance`. Mark period-wise attendance; verify that students with attendance below 75% trigger the **Attendance Shortage Warning** banner.
6. **Export**: Export filtered records to CSV (`/students/export`) and Excel (`/students/export-excel`). Download the official PDF report card (`/students/<id>/pdf`).

### 3. Test as Student (`student`)
1. Log in with a student portal account (e.g. `reg2024001` or generated registration number).
2. **Student Portal**: Verify restricted view displaying only personal coursework, semester grades, and attendance metrics.
3. **Security Test**: Attempt to access `/students/add`, `/users`, or another student's dossier (`/students/2`); confirm strict **HTTP 403 Forbidden** rejection.
4. **Self-Service**: Navigate to `/student/profile`. Update phone number or address; confirm academic marks cannot be modified.
5. **Public Verification**: Open `/verify/<token>` to view the public tamper-proof credential verification view.
