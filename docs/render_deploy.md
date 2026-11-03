# Render.com Deployment Checklist & Guide

This step-by-step checklist walks you through deploying the **Smart Student Management System** to [Render.com](https://render.com) using a Web Service on the free tier with persistent PostgreSQL and production security hardening.

---

## 📋 Pre-Deployment Prerequisites

- [ ] **GitHub Repository**: Code committed and pushed to your remote repository on the `main` branch.
- [ ] **Persistent PostgreSQL**: A free PostgreSQL database provisioned on [Neon](https://neon.tech) or [Supabase](https://supabase.com).
  > ⚠️ **Important (Ephemeral Filesystem)**: Render's free tier wipes local disk storage whenever the service sleeps or redeploys. You **must** connect an external PostgreSQL database via `DATABASE_URL` to persist student records, marks history, and audit logs. Follow [docs/postgres_setup.md](postgres_setup.md).

---

## 🛠️ Step 1: Create the Web Service on Render

1. Log in to your [Render Dashboard](https://dashboard.render.com).
2. Click **New +** in the top navigation bar and select **Web Service**.
3. Choose **Build and deploy from a Git repository** and connect your GitHub repository.
4. Configure the service settings:

| Field | Configuration Value | Notes |
| :--- | :--- | :--- |
| **Name** | `smart-student-management-system` | Or your preferred service name |
| **Region** | `Oregon (US West)` or `Ohio (US East)` | Choose the region closest to your database |
| **Branch** | `main` | Production branch |
| **Runtime** | `Python` | Python 3.12 |
| **Instance Type** | `Free` | 512 MB RAM / 0.1 CPU |
| **Build Command** | `pip install -r requirements.txt && python database/init_db.py` | Installs dependencies and runs idempotent migrations |
| **Start Command** | `gunicorn --workers 2 --threads 2 --timeout 90 --bind 0.0.0.0:$PORT app:app` | Multi-worker WSGI server reading `$PORT` |

5. Expand **Advanced** and set:
   - **Health Check Path**: `/health`

---

## 🔐 Step 2: Configure Environment Variables

Under the **Environment** section in your Render Web Service dashboard, click **Add Environment Variable** for each of the following keys:

| Variable Key | Required | Example Format | Description |
| :--- | :---: | :--- | :--- |
| `SECRET_KEY` | **Yes** | `c4f82a9d1e3b5780a2f4c6e8d0b2a4f6e8d0c2b4a6f8e0d2c4b6a8f0e2d4c6b8` | Secure 64-char key for session signing and CSRF. Generate with:<br>`python -c "import secrets; print(secrets.token_hex(32))"` |
| `ADMIN_USERNAME` | **Yes** | `admin` | Initial administrator username |
| `ADMIN_PASSWORD` | **Yes** | `<set-a-strong-password>` | Min 8 chars, 1 uppercase, 1 lowercase, 1 number, 1 symbol (e.g. `AdminSecure@2026!`) |
| `ADMIN_EMAIL` | **Yes** | `admin@institution.edu` | Administrator email for account management |
| `DATABASE_URL` | **Yes** | `postgresql://user:password@ep-cool-db.us-east-2.aws.neon.tech/neondb?sslmode=require` | Neon or Supabase PostgreSQL connection string with `?sslmode=require` |
| `FLASK_ENV` | **Yes** | `production` | Enables production security checks, HSTS, and Secure cookies |
| `FLASK_DEBUG` | **Yes** | `0` | Disables debug mode and Werkzeug interactive console |
| `STORAGE_BACKEND` | **Yes** | `local` | `local` (ephemeral) or `s3` (persistent AWS S3 bucket) |
| `RATELIMIT_STORAGE_URI` | **Yes** | `memory://` | In-memory rate limiting, or Redis URL (e.g. `redis://default:password@host:port/0`) |

### Optional AWS S3 Storage Variables (Only if `STORAGE_BACKEND=s3`)
| Variable Key | Example Format | Purpose |
| :--- | :--- | :--- |
| `AWS_ACCESS_KEY_ID` | `AKIAIOSFODNN7EXAMPLE` | IAM access key |
| `AWS_SECRET_ACCESS_KEY` | `<your-aws-secret-access-key>` | IAM secret key |
| `AWS_REGION` | `us-east-1` | S3 bucket AWS region |
| `AWS_S3_BUCKET` | `institution-student-uploads` | S3 bucket name |

---

## 🚀 Step 3: Deploy & Monitor Build Logs

1. Click **Create Web Service** (or **Manual Deploy** &rarr; **Clear build cache & deploy**).
2. Monitor the Render deployment logs. Confirm:
   - Dependencies install cleanly (`pip install -r requirements.txt`).
   - `database/init_db.py` executes successfully:
     ```text
     [+] Initial production administrator 'admin' bootstrapped successfully.
     ```
   - Gunicorn starts on the bound port:
     ```text
     [INFO] Listening at: http://0.0.0.0:10000
     [INFO] Using worker: sync
     [INFO] Booting worker with pid: ...
     ```
   - Health check passes:
     ```text
     Health check passed at /health
     Service is live at https://<your-service>.onrender.com
     ```

---

## 🔍 Step 4: Post-Deployment Verification Checklist

After the deployment reports **Live**, verify each item:

### 1. Health & Deep Readiness Check
- Open `https://<your-service>.onrender.com/health` in your browser.
- **Expected response**: HTTP 200 OK
  ```json
  {
    "status": "healthy",
    "checks": {
      "database": "connected",
      "storage": "operational"
    }
  }
  ```

### 2. HTTPS & Security Headers
- Check the URL bar shows **HTTPS** (SSL certificate active).
- Open browser DevTools &rarr; Network tab &rarr; Inspect response headers:
  - `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `Referrer-Policy: strict-origin-when-cross-origin`

### 3. Secure Session Cookie
- Log in at `https://<your-service>.onrender.com/login` using your `ADMIN_USERNAME` and `ADMIN_PASSWORD`.
- Inspect the session cookie in DevTools &rarr; Application &rarr; Cookies:
  - **Name**: `__Host-session` (Production prefix)
  - **Secure**: `true` (HTTPS only)
  - **HttpOnly**: `true` (XSS immune)
  - **SameSite**: `Lax` (CSRF defense)

### 4. Reverse Proxy & Client IP Trust (ProxyFix)
- Render acts as an edge reverse proxy. The application automatically enables `ProxyFix(x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)` when `FLASK_ENV=production`.
- This ensures:
  - `request.remote_addr` captures the actual user's client IP, not Render's internal gateway.
  - Rate limiting with `Flask-Limiter` tracks user IPs individually rather than throttling the entire service.

### 5. Idempotent Redeployment Test
- Trigger a manual redeploy in Render (**Manual Deploy** &rarr; **Deploy latest commit**).
- Verify in logs that `database/init_db.py` runs without error:
  - Tables and schema migrations are skipped or run with `ON CONFLICT DO NOTHING`.
  - Admin account is preserved without duplicating records or resetting passwords.

---

## ❓ Troubleshooting Common Render Issues

| Symptom | Probable Cause | Resolution |
| :--- | :--- | :--- |
| **Build fails on `pip install`** | Dependency conflict | Ensure `requirements.txt` is committed and Python version matches `3.12.8`. |
| **`RuntimeError: SECRET_KEY must be >= 32 chars`** | `SECRET_KEY` missing or too short | Generate a 64-char hex string via Python secrets and paste into Render environment variables. |
| **`psycopg2.OperationalError: sslmode`** | Non-SSL database string | Append `?sslmode=require` to your `DATABASE_URL`. |
| **502 Bad Gateway / Port timeout** | Hardcoded port binding | Ensure start command uses `--bind 0.0.0.0:$PORT` so Gunicorn reads the dynamic port injected by Render. |
| **Data resets after restart** | Using SQLite instead of PostgreSQL | Set `DATABASE_URL` to your Neon/Supabase PostgreSQL connection string. |
| **Rate limiter triggers immediately** | ProxyFix disabled | Set `FLASK_ENV=production` so `ProxyFix` unpacks `X-Forwarded-For` client IPs. |
