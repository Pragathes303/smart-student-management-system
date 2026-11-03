"""
Automated Email Notification Module
Supports standard SMTP and Amazon SES (Simple Email Service) integration.
Provides simulated fallback for local development when SMTP is unconfigured.
"""

import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import current_app

from backend.async_tasks import run_in_background
from backend.logger import record_audit


def get_mail_config():
    """Retrieve mail configuration settings from Flask current_app or environment."""
    app_cfg = getattr(current_app, 'config', {}) if current_app else {}

    server = app_cfg.get('SMTP_SERVER')
    if server is None:
        server = app_cfg.get('MAIL_SERVER')
    if server is None:
        server = os.getenv('MAIL_SERVER', '')

    port = app_cfg.get('SMTP_PORT') or app_cfg.get('MAIL_PORT') or os.getenv('MAIL_PORT', 587)
    username = app_cfg.get('SMTP_USERNAME') or app_cfg.get('MAIL_USERNAME') or os.getenv('MAIL_USERNAME', '')
    password = app_cfg.get('SMTP_PASSWORD') or app_cfg.get('MAIL_PASSWORD') or os.getenv('MAIL_PASSWORD', '')
    from_addr = app_cfg.get('SMTP_FROM_EMAIL') or app_cfg.get('MAIL_FROM') or os.getenv('MAIL_FROM', 'admissions@university.edu')

    enabled = app_cfg.get('MAIL_ENABLED')
    if enabled is None:
        enabled = os.getenv('MAIL_ENABLED', '0') == '1'

    if server and username and password:
        enabled = True

    return {
        'enabled': bool(enabled),
        'server': server,
        'port': int(port) if port else 587,
        'username': username,
        'password': password,
        'from_addr': from_addr
    }


def send_email_message(to_email, subject, body_text, attachment_buffer=None, attachment_filename=None, max_retries=2):
    """
    Send an email via SMTP (with retry) or record to simulation log if credentials are not configured.
    
    Returns:
        tuple: (success: bool, message: str)
    """
    cfg = get_mail_config()

    # If SMTP is not actively configured or disabled, simulate email delivery
    if not cfg['enabled'] or not cfg['username'] or not cfg['password'] or not cfg['server']:
        log_msg = f"[EMAIL SIMULATION] Sent to: '{to_email}' | Subject: '{subject}'"
        if current_app:
            current_app.logger.info(log_msg)
        record_audit(
            action="EMAIL_SIMULATED",
            details=f"Email simulated to {to_email} (Subject: {subject})",
            username="mailer"
        )
        return True, "Email notification simulated successfully (configure SMTP in .env for live dispatch)."

    # Live SMTP Dispatch with Retry (B8)
    attempts = 0
    last_error = None
    while attempts < max_retries:
        attempts += 1
        try:
            msg = MIMEMultipart()
            msg['From'] = cfg['from_addr']
            msg['To'] = to_email
            msg['Subject'] = subject

            msg.attach(MIMEText(body_text, 'plain', 'utf-8'))

            if attachment_buffer and attachment_filename:
                attachment_buffer.seek(0)
                part = MIMEApplication(attachment_buffer.read(), Name=attachment_filename)
                part['Content-Disposition'] = f'attachment; filename="{attachment_filename}"'
                msg.attach(part)

            server = smtplib.SMTP(cfg['server'], cfg['port'], timeout=10)
            server.starttls()
            server.login(cfg['username'], cfg['password'])
            server.sendmail(cfg['from_addr'], [to_email], msg.as_string())
            server.quit()

            if current_app:
                current_app.logger.info("Live email successfully dispatched to '%s'", to_email)
            record_audit(
                action="EMAIL_DISPATCHED",
                details=f"Live email sent to {to_email} (Subject: {subject})",
                username="mailer"
            )
            return True, "Email notification sent successfully."
        except Exception as e:
            last_error = e
            if current_app:
                current_app.logger.warning("Email attempt %d/%d to '%s' failed: %s", attempts, max_retries, to_email, str(e))

    err_msg = f"Failed to send email to {to_email} after {max_retries} attempts: {last_error!s}"
    if current_app:
        current_app.logger.error(err_msg)
    record_audit(
        action="EMAIL_DISPATCH_FAILED",
        details=f"Email dispatch failed to {to_email}: {last_error!s}",
        username="mailer"
    )
    return False, err_msg


def send_email_async(to_email, subject, body_text, attachment_buffer=None, attachment_filename=None):
    """Dispatch email asynchronously in background worker pool (B8)."""
    run_in_background(send_email_message, to_email, subject, body_text, attachment_buffer, attachment_filename)


def send_welcome_student_email(to_email, student_name, register_number, password_setup_link=None, temp_password=None):
    """Send welcome email with student portal login link and credentials or setup token."""
    base_url = current_app.config.get('APP_BASE_URL', 'http://127.0.0.1:5000') if current_app else 'http://127.0.0.1:5000'
    subject = f"Welcome to Student Academic Portal - {student_name}"
    
    if password_setup_link:
        cred_text = f"Password Setup Link: {password_setup_link}\n(This secure setup link is valid for 24 hours)"
    elif temp_password:
        cred_text = f"Temporary One-Time Password: {temp_password}\n(Mandatory password change required upon first sign-in)"
    else:
        cred_text = "Contact your academic administrator to receive your one-time sign-in credentials."

    body = f"""Dear {student_name},

Welcome to the Student Academic Portal. Your academic profile has been registered in the institutional system.

Your Student Portal Access Details:
------------------------------------------
Portal URL: {base_url}/login
Username (Register No): {register_number.lower()}
{cred_text}
------------------------------------------

You can sign in to view your enrolled semester grades, cumulative GPA, attendance standing, and download official PDF grade cards.

Regards,
Academic Registrar Office
Institute of Higher Education & Technology
"""
    return send_email_message(to_email, subject, body)


def send_student_marksheet_email(to_email=None, student_name=None, register_number=None, pdf_buffer=None, student_dict=None, subjects=None, attendance=None, recipient_email=None):
    """Send official grade card PDF as an email attachment."""
    target_email = recipient_email or to_email
    if not target_email and student_dict:
        target_email = student_dict.get('email')

    name = student_name or (student_dict.get('name') if student_dict else "Student")
    reg = register_number or (student_dict.get('register_number') if student_dict else "STUDENT")

    if pdf_buffer is None:
        from backend.pdf_generator import generate_student_marksheet_pdf
        data_dict = student_dict or {'name': name, 'register_number': reg, 'marks': 0.0}
        pdf_buffer = generate_student_marksheet_pdf(data_dict, subjects=subjects, attendance=attendance)

    subject = f"Official Academic Marksheet - {name} ({reg})"
    body = f"""Dear {name},

Attached is your official academic grade report for your records.

Student: {name}
Register Number: {reg}

Please find the attached PDF document for your detailed scorecard and grade evaluation.

Regards,
Office of Controller of Examinations
Institute of Higher Education & Technology
"""
    clean_reg = str(reg).replace('/', '_').replace('\\', '_')
    filename = f"{clean_reg}_marksheet.pdf"
    return send_email_message(target_email, subject, body, attachment_buffer=pdf_buffer, attachment_filename=filename)


def send_marks_published_email(to_email: str, student_name: str, register_number: str, semester: int, sgpa: float = None, cgpa: float = None):
    """
    Send an email notification to the student when semester marks/results are officially published.
    Supports live SMTP dispatch with automated simulation fallback (Phase 3).
    """
    base_url = current_app.config.get('APP_BASE_URL', 'http://127.0.0.1:5000') if current_app else 'http://127.0.0.1:5000'
    subject = f"Official Results Published: Semester {semester} - {student_name}"

    score_details = ""
    if sgpa is not None:
        score_details += f"Semester {semester} SGPA: {sgpa:.2f} / 10.0\n"
    if cgpa is not None:
        score_details += f"Cumulative CGPA: {cgpa:.2f} / 10.0\n"

    body = f"""Dear {student_name},

Your official academic results for Semester {semester} have been approved and published by the Office of the Controller of Examinations.

Student Name: {student_name}
Register Number: {register_number}
Semester: {semester}
{score_details}
You can log in to your Student Portal to view detailed course scores, credit evaluation, and download your official grade card PDF:
{base_url}/student-portal

Regards,
Office of the Controller of Examinations
Institute of Higher Education & Technology
"""
    return send_email_message(to_email, subject, body)

