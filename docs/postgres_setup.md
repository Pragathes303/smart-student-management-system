# PostgreSQL Setup Guide (Free-Tier Cloud Database)

This guide walks you through provisioning a free, persistent PostgreSQL database on **Neon** or **Supabase** and connecting it to the **Smart Student Management System** on Render (or local development).

---

## Why PostgreSQL is Needed for Render Free Tier

Render's free tier web services utilize an **ephemeral filesystem**. Any files written to the local disk (such as `students.db` in SQLite) are **wiped out** whenever the application sleeps, restarts, or deploys a new commit. 

Connecting an external managed PostgreSQL database via the `DATABASE_URL` environment variable guarantees that your student records, marks history, audit logs, and credentials remain **fully persistent and durable**.

---

## Option A: Neon Serverless PostgreSQL (Recommended)

[Neon](https://neon.tech) offers a generous free tier with 0.5 GB storage, instant branching, and serverless auto-suspend.

### Step 1: Create a Neon Account & Project
1. Navigate to [https://neon.tech](https://neon.tech) and sign up with GitHub or Google.
2. Click **Create Project**.
3. Name your project (e.g., `student-management-db`).
4. Select your preferred AWS Region (choose **US East (Ohio / Oregon)** to match Render's free tier region for lowest latency).
5. Choose PostgreSQL version `16` (or latest).
6. Click **Create Project**.

### Step 2: Obtain Connection String
1. On the Neon Dashboard, locate the **Connection Details** card.
2. Select **Connection string** and choose **psycopg2** or **PostgreSQL**.
3. Copy the URL. It will look like:
   ```text
   postgresql://alex:AbCdEf123456@ep-cool-snowflake-123456.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
4. **Ensure `sslmode=require` is present** at the end of the query string. Neon rejects non-SSL connections.

---

## Option B: Supabase PostgreSQL

[Supabase](https://supabase.com) provides 500 MB of persistent PostgreSQL storage on their free tier.

### Step 1: Create a Supabase Project
1. Go to [https://supabase.com](https://supabase.com) and sign in.
2. Click **New Project** and select your organization.
3. Enter project details:
   - **Name**: `student-management-system`
   - **Database Password**: Generate and securely save a strong password.
   - **Region**: Choose a region closest to your Render service (e.g., `US East (N. Virginia)` or `US West (Oregon)`).
4. Click **Create new project** and wait 1-2 minutes for provisioning.

### Step 2: Retrieve the Connection String
1. In the left navigation bar, go to **Project Settings** (gear icon) -> **Database**.
2. Scroll to the **Connection string** section.
3. Select the **URI** tab.
4. Copy the connection string and replace `[YOUR-PASSWORD]` with your database password:
   ```text
   postgresql://postgres.yourprojectid:yourpassword@aws-0-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require
   ```
5. Ensure `?sslmode=require` is appended.

---

## Connection String Compatibility & Features

The Smart Student Management System's database engine ([database/db.py](file:///c:/cloud%20project/database/db.py)) includes built-in compatibility layers:

1. **URL Normalization**:
   - Both `postgresql://` and legacy `postgres://` prefixes are automatically recognized and converted to `postgresql://` for `psycopg2`.
2. **Dynamic Placeholder Translation**:
   - SQL queries using standard `?` parameter markers are automatically converted to PostgreSQL's `%s` format.
3. **`RETURNING id` Auto-Injection**:
   - `INSERT` statements automatically append `RETURNING id` on PostgreSQL to capture `lastrowid` transparently across both SQLite and PostgreSQL.
4. **SSL Enforcement**:
   - Cloud providers require TLS. Always keep `?sslmode=require` in your connection string.

---

## Configuring on Render

### Step 1: Add Environment Variables
1. Log in to your [Render Dashboard](https://dashboard.render.com).
2. Select your Web Service (`smart-student-management-system`).
3. Click **Environment** in the left sidebar.
4. Add the following environment variables:

| Key | Value | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://user:pass@host/dbname?sslmode=require` | Your Neon or Supabase connection string |
| `ADMIN_USERNAME` | `<choose-admin-username>` | Production administrator username |
| `ADMIN_PASSWORD` | `<choose-a-strong-password>` | Production administrator password (min 8 chars, 1 uppercase, 1 digit) |
| `ADMIN_EMAIL` | `admin@institution.edu` | Administrator email |
| `FLASK_ENV` | `production` | Enables production security mode |
| `FLASK_DEBUG` | `0` | Disables debug mode |

### Step 2: Deployment & Migration Idempotency
During every deploy, Render executes the `buildCommand` defined in [render.yaml](file:///c:/cloud%20project/render.yaml):
```bash
pip install --upgrade pip && pip install -r requirements.txt && python database/init_db.py
```

`database/init_db.py` is **fully idempotent**:
- It creates all 10 schema tables and indices if they do not already exist (`CREATE TABLE IF NOT EXISTS`).
- It seeds academic departments and system configuration using `ON CONFLICT DO NOTHING`.
- It checks if an admin user already exists (`SELECT COUNT(*) FROM users WHERE role = 'admin'`). If an admin exists, **it does nothing**—it will never duplicate users, reset passwords, or overwrite customized institutional data.

---

## Running Locally Against PostgreSQL

To test your application against PostgreSQL locally or verify a remote connection before deploying:

```bash
# 1. Set environment variable
export DATABASE_URL="postgresql://user:pass@host:5432/dbname?sslmode=require"
# On Windows PowerShell:
# $env:DATABASE_URL="postgresql://user:pass@host:5432/dbname?sslmode=require"

# 2. Run the database initialization script
python database/init_db.py

# 3. Start the Flask server
python run.py
```
