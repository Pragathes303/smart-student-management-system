"""
Validation and Helper Utilities
Provides strict input validation, grade calculations, and security helpers.
"""

import re

from backend.grading import calculate_cgpa as centralized_calculate_cgpa
from backend.grading import get_grade_info
from database.db import query_db

EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$')
REG_NO_REGEX = re.compile(r'^[A-Za-z0-9\-_]{3,25}$')
USERNAME_REGEX = re.compile(r'^[A-Za-z0-9_]{3,30}$')
PHONE_REGEX = re.compile(r'^\+?[0-9\s\-()]{7,20}$')
NAME_REGEX = re.compile(r"^[A-Za-z\s.'\-]{2,80}$")


def validate_phone_number(phone_raw: str | None) -> tuple[bool, str]:
    """
    Validate and sanitize phone number.
    Accepts international and national formats: optional leading +, digits, hyphens, spaces.
    Must contain between 7 and 15 digits.
    Returns: (is_valid: bool, error_or_cleaned: str)
    """
    if not phone_raw:
        return True, ""
    phone = str(phone_raw).strip()
    if not phone:
        return True, ""
    if not PHONE_REGEX.match(phone):
        return False, "Invalid phone number format. Provide 7-15 digits (e.g., +1 555-123-4567 or 9876543210)."
    digits = re.sub(r'\D', '', phone)
    if len(digits) < 7 or len(digits) > 15:
        return False, "Phone number must contain between 7 and 15 digits."
    return True, phone


def validate_date_of_birth(dob_raw: str | None) -> tuple[bool, str]:
    """
    Validate date of birth.
    Must be ISO format YYYY-MM-DD, a valid calendar date, strictly in the past, and realistic.
    Returns: (is_valid: bool, error_or_cleaned: str)
    """
    from datetime import date, datetime
    if not dob_raw:
        return True, ""
    dob = str(dob_raw).strip()
    if not dob:
        return True, ""
    try:
        dt = datetime.strptime(dob, "%Y-%m-%d").date()
    except ValueError:
        return False, "Date of birth must be a valid calendar date in YYYY-MM-DD format."
    today = date.today()
    if dt >= today:
        return False, "Date of birth must be strictly in the past."
    if dt.year < 1900 or (today.year - dt.year) > 120:
        return False, "Date of birth year is outside the acceptable range (1900 - present)."
    return True, dob


def allowed_file(filename, allowed_extensions=None):
    """Check if uploaded file has an allowed image extension."""
    if allowed_extensions is None:
        allowed_extensions = {'png', 'jpg', 'jpeg', 'webp'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions


def get_valid_departments():
    """
    Retrieve list of valid institutional departments from departments table or Config (B5).
    """
    try:
        rows = query_db("SELECT name FROM departments ORDER BY name")
        if rows:
            return [r['name'] for r in rows]
    except Exception:
        pass
    from backend.config import Config
    return list(Config.DEPARTMENTS)


def calculate_grade(marks):
    """
    Calculate academic letter grade and descriptive status from marks (0-100).
    Uses Anna University 10-point scale centralized in backend.grading (B11).
    """
    info = get_grade_info(marks)
    return {
        'grade': info['letter'],
        'letter': info['letter'],
        'grade_point': info['grade_point'],
        'points': info['grade_point'],
        'label': info['description'],
        'badge_class': info['css_class'],
        'status': info['status'],
        'passed': info['is_pass'],
        'is_pass': info['is_pass']
    }


def calculate_subject_grade_point(marks):
    """
    Calculate Anna University 10-point scale grade point and letter grade (B11).
    Returns: (grade_point: float, letter_grade: str)
    """
    info = get_grade_info(marks)
    return info['grade_point'], info['letter']


def calculate_gpa(subjects):
    """
    Calculate Grade Point Average (GPA) across a list of subject records.
    Centralized in backend.grading (B11).
    """
    return centralized_calculate_cgpa(subjects)


def get_attendance_status(percentage):
    """
    Evaluate institutional attendance threshold.
    Standard rule: Minimum 75% required for examination eligibility.
    """
    try:
        val = float(percentage)
    except (ValueError, TypeError):
        val = 0.0

    if val >= 85.0:
        return {'label': 'Good (Eligible)', 'badge': 'badge-grade-a', 'badge_class': 'badge-grade-a', 'is_shortage': False, 'status': 'Satisfactory', 'alert_level': 'success'}
    elif val >= 75.0:
        return {'label': 'Adequate (Eligible)', 'badge': 'badge-grade-b', 'badge_class': 'badge-grade-b', 'is_shortage': False, 'status': 'Adequate', 'alert_level': 'info'}
    elif val >= 65.0:
        return {'label': 'Shortage Alert (Condonation Req)', 'badge': 'badge-grade-d', 'badge_class': 'badge-grade-d', 'is_shortage': True, 'status': 'Warning', 'alert_level': 'warning'}
    else:
        return {'label': 'Detained (Severe Shortage)', 'badge': 'badge-grade-f', 'badge_class': 'badge-grade-f', 'is_shortage': True, 'status': 'Detained', 'alert_level': 'danger'}



def validate_student_data(
    name,
    register_number,
    department,
    email,
    marks_raw,
    current_student_id=None,
    phone=None,
    dob=None,
    year=None
):
    """
    Validate all fields submitted for a student record.
    Uses parameterized database queries to ensure uniqueness of register number and email.
    
    Returns:
        tuple: (is_valid: bool, errors: list[str], cleaned_data: dict)
    """
    errors = []
    cleaned = {}

    # 1. Student Name validation
    name_clean = (name or '').strip()
    if not name_clean:
        errors.append("Student Name is required.")
    elif len(name_clean) < 2 or len(name_clean) > 80:
        errors.append("Student Name must be between 2 and 80 characters.")
    elif not NAME_REGEX.match(name_clean):
        errors.append("Student Name must contain only letters, spaces, hyphens, and periods (2-80 characters).")
    cleaned['name'] = name_clean

    # 2. Register Number validation
    reg_clean = (register_number or '').strip().upper()
    if not reg_clean:
        errors.append("Register Number is required.")
    elif not REG_NO_REGEX.match(reg_clean):
        errors.append("Register Number must be 3-25 alphanumeric characters (hyphens and underscores allowed).")
    else:
        # Check uniqueness with parameterized query
        if current_student_id:
            duplicate = query_db(
                "SELECT id FROM students WHERE register_number = ? AND id != ?",
                (reg_clean, current_student_id),
                one=True
            )
        else:
            duplicate = query_db(
                "SELECT id FROM students WHERE register_number = ?",
                (reg_clean,),
                one=True
            )
        if duplicate:
            errors.append(f"A student with Register Number '{reg_clean}' already exists.")
    cleaned['register_number'] = reg_clean

    # 3. Department validation (B5 Whitelist)
    dept_clean = (department or '').strip()
    valid_departments = get_valid_departments()
    if not dept_clean:
        errors.append("Department is required.")
    elif dept_clean not in valid_departments:
        errors.append(f"Invalid department '{dept_clean}'. Allowed departments: {', '.join(sorted(valid_departments))}.")
    cleaned['department'] = dept_clean

    # 4. Email validation
    email_clean = (email or '').strip().lower()
    if not email_clean:
        errors.append("Student Email is required.")
    elif not EMAIL_REGEX.match(email_clean):
        errors.append("Please provide a valid email address (e.g. student@university.edu).")
    elif len(email_clean) > 120:
        errors.append("Email cannot exceed 120 characters.")
    else:
        # Check email uniqueness with parameterized query
        if current_student_id:
            dup_email = query_db(
                "SELECT id FROM students WHERE email = ? AND id != ? AND deleted_at IS NULL",
                (email_clean, current_student_id),
                one=True
            )
        else:
            dup_email = query_db(
                "SELECT id FROM students WHERE email = ? AND deleted_at IS NULL",
                (email_clean,),
                one=True
            )
        if dup_email:
            errors.append(f"A student with email '{email_clean}' already exists.")
    cleaned['email'] = email_clean

    # 5. Marks validation (0 - 100)
    try:
        marks_float = float(marks_raw)
        if marks_float < 0.0 or marks_float > 100.0:
            errors.append("Marks must be between 0.0 and 100.0.")
        else:
            cleaned['marks'] = round(marks_float, 2)
    except (ValueError, TypeError):
        errors.append("Marks must be a valid numeric value between 0.0 and 100.0.")
        cleaned['marks'] = 0.0

    # 6. Academic Year validation (1 - 4)
    if year is not None and str(year).strip():
        try:
            year_int = int(year)
            if year_int < 1 or year_int > 4:
                errors.append("Academic year must be between 1 and 4.")
            else:
                cleaned['year'] = year_int
        except (ValueError, TypeError):
            errors.append("Academic year must be a valid number between 1 and 4.")
            cleaned['year'] = 1
    else:
        cleaned['year'] = 1

    # 7. Phone validation (optional)
    if phone is not None:
        is_phone_valid, phone_val = validate_phone_number(phone)
        if not is_phone_valid:
            errors.append(phone_val)
        cleaned['phone'] = phone_val if (is_phone_valid and phone_val) else None

    # 8. Date of Birth validation (optional)
    if dob is not None:
        is_dob_valid, dob_val = validate_date_of_birth(dob)
        if not is_dob_valid:
            errors.append(dob_val)
        cleaned['dob'] = dob_val if (is_dob_valid and dob_val) else None

    return len(errors) == 0, errors, cleaned


def validate_user_data(username, password, role, full_name, email):
    """
    Validate user registration fields for role-based system accounts.
    Uses parameterized queries for uniqueness.
    """
    errors = []
    cleaned = {}

    # Username
    u_clean = (username or '').strip().lower()
    if not u_clean or not USERNAME_REGEX.match(u_clean):
        errors.append("Username must be 3-30 alphanumeric characters.")
    else:
        existing = query_db("SELECT id FROM users WHERE username = ?", (u_clean,), one=True)
        if existing:
            errors.append(f"Username '{u_clean}' is already in use.")
    cleaned['username'] = u_clean

    # Password
    from backend.security import validate_password_strength
    is_strong, pwd_err = validate_password_strength(password, username=u_clean)
    if not is_strong:
        errors.append(pwd_err)
    cleaned['password'] = password

    # Role
    r_clean = (role or '').strip().lower()
    if r_clean not in ('admin', 'staff', 'student'):
        errors.append("Role must be 'admin', 'staff', or 'student'.")
    cleaned['role'] = r_clean

    # Full Name
    fn_clean = (full_name or '').strip()
    if not fn_clean or len(fn_clean) < 2:
        errors.append("Full Name is required (minimum 2 characters).")
    cleaned['full_name'] = fn_clean

    # Email
    em_clean = (email or '').strip().lower()
    if not em_clean or not EMAIL_REGEX.match(em_clean):
        errors.append("Please provide a valid email address.")
    else:
        existing_email = query_db("SELECT id FROM users WHERE email = ?", (em_clean,), one=True)
        if existing_email:
            errors.append(f"Email '{em_clean}' is already registered.")
    cleaned['email'] = em_clean

    return len(errors) == 0, errors, cleaned


def map_errors_to_fields(errors: list[str]) -> dict[str, str]:
    """
    Map raw validation error strings to specific form field names
    for contextual inline error rendering.
    """
    field_errors: dict[str, str] = {}
    for err in errors:
        lower = err.lower()
        if 'name' in lower and 'username' not in lower and 'name' not in field_errors:
            field_errors['name'] = err
        elif ('register number' in lower or 'reg no' in lower or 'registration' in lower) and 'register_number' not in field_errors:
            field_errors['register_number'] = err
        elif 'department' in lower and 'department' not in field_errors:
            field_errors['department'] = err
        elif 'year' in lower and 'year' not in field_errors:
            field_errors['year'] = err
        elif 'email' in lower and 'email' not in field_errors:
            field_errors['email'] = err
        elif ('mark' in lower or 'score' in lower) and 'marks' not in field_errors:
            field_errors['marks'] = err
        elif 'phone' in lower and 'phone' not in field_errors:
            field_errors['phone'] = err
        elif ('birth' in lower or 'dob' in lower) and 'dob' not in field_errors:
            field_errors['dob'] = err
        elif ('photo' in lower or 'image' in lower) and 'photo' not in field_errors:
            field_errors['photo'] = err
        elif 'username' in lower and 'username' not in field_errors:
            field_errors['username'] = err
        elif 'password' in lower and 'password' not in field_errors:
            field_errors['password'] = err
    return field_errors

