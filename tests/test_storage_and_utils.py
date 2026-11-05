"""
Unit Tests for Storage, Utils, Security, and Grading
Provides targeted unit test coverage for edge cases, validation functions, and local file storage.
"""

import io

from PIL import Image

from backend.grading import format_cgpa_report, get_grade_info
from backend.security import (
    desanitize_csv_cell,
    generate_temp_password,
    is_safe_redirect,
    sanitize_csv_cell,
    validate_and_sanitize_image,
    validate_password_strength,
)
from backend.storage import LocalStorageHandler, S3StorageHandler
from backend.utils import (
    allowed_file,
    get_attendance_status,
    validate_user_data,
)


def test_local_storage_lifecycle(tmp_path):
    """Test LocalStorageHandler save, get_url, and delete operations."""
    storage = LocalStorageHandler(upload_folder=str(tmp_path))

    # Save a file
    class DummyFile:
        def __init__(self, filename, content):
            self.filename = filename
            self.content = content
        def save(self, dest):
            with open(dest, 'wb') as f:
                f.write(self.content)

    dummy = DummyFile("test_doc.txt", b"Sample student document data")
    filename = storage.save(dummy)
    assert filename.endswith("test_doc.txt")
    assert storage.is_cloud_active() is False

    # Retrieve file URL
    url = storage.get_url(filename)
    assert filename in url

    # Delete file
    assert storage.delete(filename) is True
    assert storage.delete("non_existent_file.txt") is True


def test_s3_storage_initialization():
    """Test S3StorageHandler instantiation without credentials."""
    s3 = S3StorageHandler(bucket_name="test-bucket", region="us-east-1", access_key="key", secret_key="secret")
    assert s3.bucket_name == "test-bucket"
    assert s3.region == "us-east-1"
    assert s3.is_cloud_active() is True


def test_security_redirect_validation():
    """Test is_safe_redirect against attack vectors."""
    assert is_safe_redirect("/dashboard") is True
    assert is_safe_redirect("/students?dept=CSE") is True
    assert is_safe_redirect(None) is False
    assert is_safe_redirect("") is False
    assert is_safe_redirect("//evil.com") is False
    assert is_safe_redirect("\\\\evil.com") is False
    assert is_safe_redirect("https://evil.com/phish") is False
    assert is_safe_redirect("javascript:alert(1)") is False
    assert is_safe_redirect("/\\evil.com") is False


def test_security_csv_sanitization():
    """Test CSV formula injection prevention and desanitization."""
    assert sanitize_csv_cell("=1+1") == "'=1+1"
    assert sanitize_csv_cell("+cmd|' /C calc'!A0") == "'+cmd|' /C calc'!A0"
    assert sanitize_csv_cell("-5+2") == "'-5+2"
    assert sanitize_csv_cell("@SUM(1,2)") == "'@SUM(1,2)"
    assert sanitize_csv_cell("Normal Text") == "Normal Text"
    assert sanitize_csv_cell(None) == ""

    assert desanitize_csv_cell("'=1+1") == "=1+1"
    assert desanitize_csv_cell("Normal") == "Normal"
    assert desanitize_csv_cell("") == ""


def test_image_validation_and_sanitization():
    """Test Pillow image validation, metadata stripping, and rejection of corrupt data."""
    # Valid RGB image
    img = Image.new('RGB', (100, 100), color='blue')
    img_buf = io.BytesIO()
    img.save(img_buf, format='JPEG')
    img_buf.seek(0)
    img_buf.filename = "avatar.jpg"

    is_valid, err, sanitized = validate_and_sanitize_image(img_buf)
    assert is_valid is True
    assert err is None
    assert sanitized is not None

    # Corrupt / non-image data
    corrupt = io.BytesIO(b"not an image binary payload")
    corrupt.filename = "bad.jpg"
    is_valid, err, sanitized = validate_and_sanitize_image(corrupt)
    assert is_valid is False
    assert "Invalid or corrupted image" in err

    # None file
    is_valid, err, sanitized = validate_and_sanitize_image(None)
    assert is_valid is False


def test_password_policy_and_generation():
    """Test generate_temp_password and validate_password_strength."""
    pwd = generate_temp_password(14)
    assert len(pwd) == 14

    assert validate_password_strength("Short1!")[0] is False
    assert validate_password_strength("nouppercase123!")[0] is False
    assert validate_password_strength("NOLOWERCASE123!")[0] is False
    assert validate_password_strength("NoDigitsHere!@#")[0] is False
    assert validate_password_strength("NoSpecialChar123")[0] is False
    assert validate_password_strength("ValidPassword@2026")[0] is True


def test_validation_utilities(app):
    """Test utility validation routines."""
    with app.app_context():
        assert allowed_file("photo.jpg") is True
        assert allowed_file("document.pdf") is False
        assert allowed_file("script.sh") is False

        att_good = get_attendance_status(90.0)
        assert att_good['is_shortage'] is False

        att_short = get_attendance_status(60.0)
        assert att_short['is_shortage'] is True

        # Validate user data
        valid, errs, cl = validate_user_data("newuser", "Password@123", "staff", "Full Name", "user@test.edu")
        assert valid is True
        assert len(errs) == 0

        invalid, errs, _ = validate_user_data("us", "123", "invalid_role", "", "not-an-email")
        assert invalid is False
        assert len(errs) > 0


def test_grading_module_comprehensive():
    """Test Anna University grading utilities and formatted summaries."""
    info_o = get_grade_info(95.0)
    assert info_o['letter'] == 'O'
    assert info_o['grade_point'] == 10.0

    info_ra = get_grade_info(45.0)
    assert info_ra['letter'] == 'RA'
    assert info_ra['grade_point'] == 0.0

    # Test report formatting
    subjects = [
        {'subject_name': 'Operating Systems', 'credits': 4, 'marks': 88, 'letter_grade': 'A+', 'grade_point': 9.0},
        {'subject_name': 'Algorithms', 'credits': 4, 'marks': 92, 'letter_grade': 'O', 'grade_point': 10.0}
    ]
    report = format_cgpa_report(subjects)
    assert "CGPA:" in report
    assert "Operating Systems" in report
