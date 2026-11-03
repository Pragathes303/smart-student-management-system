#!/bin/bash
# ==============================================================================
# Quick Zero-Downtime Deployment & Update Script for AWS EC2
# Usage on EC2: bash cloud/deploy.sh
# ==============================================================================

set -e

echo "[*] Pulling latest changes from git repository..."
git pull origin main || git pull origin master

echo "[*] Activating Python virtual environment..."
source venv/bin/activate

echo "[*] Updating Python packages..."
pip install -r requirements.txt

echo "[*] Running database migrations..."
python3 database/init_db.py

echo "[*] Restarting Gunicorn systemd service..."
sudo systemctl restart student_system

echo "[*] Checking service status..."
sudo systemctl status student_system --no-pager

echo "[✓] Deployment completed successfully!"
