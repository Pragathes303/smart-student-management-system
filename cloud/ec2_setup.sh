#!/bin/bash
# ==============================================================================
# AWS EC2 Setup and Deployment Script for Smart Student Management System
# Target OS: Ubuntu 22.04 LTS / 24.04 LTS
# ==============================================================================

set -e

echo "============================================================"
echo " Starting AWS EC2 Setup for Smart Student Management System"
echo "============================================================"

APP_DIR="/var/www/student_system"
APP_USER="ubuntu"

# 1. Update OS packages
echo "[1/7] Updating system packages..."
sudo apt-get update -y
sudo apt-get upgrade -y

# 2. Install Python, pip, virtual environment, Nginx, and SQLite
echo "[2/7] Installing system dependencies..."
sudo apt-get install -y python3 python3-pip python3-venv nginx sqlite3 git ufw

# 3. Create Application Directory
echo "[3/7] Setting up application directory at $APP_DIR..."
sudo mkdir -p $APP_DIR
sudo chown -R $APP_USER:$APP_USER $APP_DIR

# 4. Clone or copy code into $APP_DIR (if run manually)
echo "[4/7] Setting up Python virtual environment..."
cd $APP_DIR
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

# Activate venv and install dependencies
source venv/bin/activate
pip install --upgrade pip
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
    pip install gunicorn
fi

# 5. Initialize SQLite Database
echo "[5/7] Initializing SQLite Database..."
python3 database/init_db.py

# 6. Configure Systemd Service for Gunicorn
echo "[6/7] Installing Systemd Service..."
sudo cp cloud/student_system.service /etc/systemd/system/student_system.service
sudo systemctl daemon-reload
sudo systemctl enable student_system
sudo systemctl restart student_system

# 7. Configure Nginx Reverse Proxy
echo "[7/7] Configuring Nginx Reverse Proxy..."
sudo cp cloud/nginx.conf /etc/nginx/sites-available/student_system
sudo ln -sf /etc/nginx/sites-available/student_system /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx

# Configure Firewall
sudo ufw allow 'Nginx Full'
sudo ufw allow 'OpenSSH'

echo "============================================================"
echo " AWS EC2 Deployment Complete!"
echo " Access your system via your EC2 Public IP or DNS: http://<EC2_PUBLIC_IP>"
echo " Initial admin credentials configured via ADMIN_USERNAME in .env"
echo "============================================================"
