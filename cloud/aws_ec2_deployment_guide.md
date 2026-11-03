# Complete AWS EC2 & Amazon S3 Deployment Guide
## Smart Student Management System

This comprehensive guide walks you through deploying the **Smart Student Management System** on **AWS EC2 (Elastic Compute Cloud)** with **Amazon S3 (Simple Storage Service)** for cloud file storage.

---

## 🏗️ Cloud Architecture Diagram

```
                 Internet / Browser
                         │
                         ▼
        ┌──────────────────────────────────┐
        │        AWS Security Group        │
        │  Port 80 (HTTP) / Port 22 (SSH)  │
        └────────────────┬─────────────────┘
                         │
                         ▼
        ┌──────────────────────────────────┐
        │          AWS EC2 Instance        │
        │        (Ubuntu 22.04 / 24.04)    │
        │                                  │
        │   ┌──────────────────────────┐   │
        │   │    Nginx (Port 80)       │   │
        │   │ (Reverse Proxy & Static) │   │
        │   └────────────┬─────────────┘   │
        │                │                 │
        │                ▼                 │
        │   ┌──────────────────────────┐   │
        │   │  Gunicorn WSGI Server    │   │
        │   │     (Port 8000 / 5000)   │   │
        │   └────────────┬─────────────┘   │
        │                │                 │
        │                ▼                 │
        │   ┌──────────────────────────┐   │
        │   │  Flask Core Application  │   │
        │   │   (Smart Student System) │   │
        │   └────────┬───────────┬─────┘   │
        │            │           │         │
        │            ▼           │         │
        │   ┌─────────────────┐  │         │
        │   │ SQLite Database │  │         │
        │   │ (students.db)   │  │         │
        │   └─────────────────┘  │         │
        └────────────────────────┼─────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │    Amazon S3 Bucket     │
                    │  (Cloud Photo Storage)  │
                    └─────────────────────────┘
```

---

## Part 1: Amazon S3 Bucket Setup (Cloud Storage)

### Step 1.1: Create S3 Bucket
1. Open the [AWS S3 Console](https://s3.console.aws.amazon.com/).
2. Click **Create bucket**.
3. **Bucket Name:** Enter a globally unique name, e.g. `student-portal-assets-2026`
4. **AWS Region:** Select `ap-south-1` (Asia Pacific - Mumbai) or `us-east-1` (US East).
5. **Block Public Access settings:** Keep **Block all public access** checked (our code uses secure pre-signed URLs, so you do NOT need to make your bucket public).
6. Click **Create bucket**.

### Step 1.2: Create IAM User & Access Keys
1. Open the [AWS IAM Console](https://console.aws.amazon.com/iam/).
2. Navigate to **Users** &rarr; **Create user**.
3. **User name:** `student-system-s3-user`
4. Select **Attach policies directly** &rarr; Search and attach `AmazonS3FullAccess`.
5. Click **Create user**.
6. Select the user &rarr; Go to **Security credentials** tab &rarr; Click **Create access key**.
7. Choose **Application running outside AWS** &rarr; Click **Next** &rarr; **Create access key**.
8. Save your **Access Key ID** and **Secret Access Key**.

### Step 1.3: Verify Connection Using the Diagnostic Tool
In your project directory, update `.env`:
```env
STORAGE_BACKEND=s3
AWS_ACCESS_KEY_ID=AKIA...YOUR_ACCESS_KEY...
AWS_SECRET_ACCESS_KEY=wJalr...YOUR_SECRET_KEY...
AWS_REGION=ap-south-1
AWS_S3_BUCKET=student-portal-assets-2026
```
Then run the verification script:
```bash
python cloud/test_s3_connection.py
```
If you see `RESULT: AWS S3 INTEGRATION IS 100% OPERATIONAL!`, your S3 cloud storage is ready!

---

## Part 2: AWS EC2 Instance Setup (Free Tier)

### Step 2.1: Launch EC2 Instance
1. Open the [AWS EC2 Console](https://console.aws.amazon.com/ec2/).
2. Click **Launch instance**.
3. **Name:** `Smart-Student-Management-System`
4. **Application and OS Images (AMI):** Select **Ubuntu 24.04 LTS** (Free Tier eligible).
5. **Instance Type:** Select `t2.micro` (or `t3.micro` depending on region - Free Tier eligible).
6. **Key pair (login):**
   - Click **Create new key pair**.
   - Key pair name: `student-system-key`.
   - Private key file format: `.pem` (for OpenSSH).
   - Click **Create key pair** (a `.pem` file will download to your computer).
7. **Network settings (Firewall / Security Group):**
   - Check **Allow SSH traffic from Anywhere** (or your specific IP).
   - Check **Allow HTTP traffic from the internet** (Port 80 for Nginx).
   - Check **Allow HTTPS traffic from the internet** (Port 443 for SSL/TLS).
   *(Note: Do NOT open port 5000 for standard Nginx deployments; Nginx handles port 80 and proxies internally).*
8. Click **Launch instance**.

---

## Part 3: Deploying on EC2

Once your EC2 instance shows status **Running**, copy its **Public IPv4 address** (e.g., `13.233.50.120`).

### Connect to EC2 via SSH:
Open Terminal / PowerShell on your computer where `student-system-key.pem` is located:
```bash
chmod 400 student-system-key.pem   # (Mac/Linux only)
ssh -i "student-system-key.pem" ubuntu@<YOUR_EC2_PUBLIC_IP>
```

---

### Option A: Deploy via Docker (Recommended - Fast & Easy)

> [!WARNING]
> **Security Group Port 5000 Rule (Docker Direct Access Only)**:
> If accessing Docker directly on Port 5000 without Nginx reverse proxy, you must add an Inbound Rule in your EC2 Security Group: **Custom TCP**, Port `5000`, Source `0.0.0.0/0` (or preferably restricted to your administrative IP).
> **Security Caution**: Exposing Port 5000 directly to `0.0.0.0/0` bypasses Nginx reverse proxy buffering, static asset caching, and security headers. For production deployments, always front Docker with Nginx (Port 80/443) or an AWS Application Load Balancer (ALB).

On your EC2 Ubuntu terminal:

```bash
# 1. Install Docker & Docker Compose
sudo apt-get update -y
sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker ubuntu
newgrp docker

# 2. Clone the repository
git clone https://github.com/<your-username>/student-management-system.git
cd student-management-system

# 3. Create .env file with your credentials
cp .env.example .env
nano .env   # (Save your SECRET_KEY and S3 credentials)

# 4. Start application with one command
docker compose up -d --build

# 5. Check logs to verify
docker compose logs -f
```

✅ **Your application is now LIVE at:** `http://<YOUR_EC2_PUBLIC_IP>:5000`

---

### Option B: Deploy via Nginx + Systemd (Native Linux Service)

On your EC2 Ubuntu terminal:

```bash
# 1. Clone repository into /var/www/student_system
sudo mkdir -p /var/www/student_system
sudo chown -R ubuntu:ubuntu /var/www/student_system
git clone https://github.com/<your-username>/student-management-system.git /var/www/student_system
cd /var/www/student_system

# 2. Configure .env file
cp .env.example .env
nano .env

# 3. Run automated setup script
bash cloud/ec2_setup.sh
```

The script will automatically:
1. Install Python 3, pip, venv, Nginx, and SQLite.
2. Create and activate a Python virtual environment.
3. Install dependencies including Gunicorn & Boto3.
4. Initialize the SQLite database with seed data.
5. Create and start the `student_system.service` systemd daemon.
6. Configure Nginx reverse proxy on Port 80.
7. Open the firewall for HTTP and SSH.

✅ **Your application is now LIVE on standard Port 80 at:** `http://<YOUR_EC2_PUBLIC_IP>`

---

## Part 4: Domain & Free SSL (Optional - HTTPS)

If you have a domain name (e.g. `students.yourdomain.com`) pointing to your EC2 IP:

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d students.yourdomain.com
```
Certbot will automatically install a free Let's Encrypt SSL certificate and configure HTTPS redirection!

---

## Part 5: Maintenance & Operational Commands

| Action | Native Systemd Command | Docker Command |
| :--- | :--- | :--- |
| **Restart App** | `sudo systemctl restart student_system` | `docker compose restart` |
| **View Live Logs** | `sudo journalctl -u student_system -f` | `docker compose logs -f` |
| **Check App Status** | `sudo systemctl status student_system` | `docker compose ps` |
| **Restart Nginx** | `sudo systemctl restart nginx` | N/A |
| **Update Code** | `git pull && sudo systemctl restart student_system` | `git pull && docker compose up -d --build` |
