"""
Bulk Student Ingestion Service (CSV and Excel)
Phase 3 Features:
- Downloadable templates for both CSV and Excel (.xlsx)
- Multi-format ingestion (.csv, .xlsx)
- Row-by-row comprehensive validation
- Preview capability before final database persistence
- Detailed execution summary (added, skipped, error messages)
"""

import csv
import io
import json
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

from backend.grading import get_grade_and_points
from backend.services.student_service import StudentService
from backend.utils import validate_student_data
from database.db import query_db


class ImportService:
    """Service handling bulk student onboarding via CSV and Excel."""

    REQUIRED_HEADERS = {'name', 'register number', 'department', 'email', 'marks'}

    @classmethod
    def generate_csv_template(cls) -> str:
        """Generate official CSV import template with standard columns and sample rows."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Name", "Register Number", "Department", "Email", "Marks", "Year"])
        writer.writerow(["Rahul Nair", "REG2024101", "Computer Science", "rahul.nair@example.edu", "86.5", "1"])
        writer.writerow(["Pooja Sundaram", "REG2024102", "Information Technology", "pooja.s@example.edu", "91.0", "2"])
        writer.writerow(["Manoj Kumar", "REG2024103", "Data Science", "manoj.k@example.edu", "74.0", "1"])
        return output.getvalue()

    @classmethod
    def generate_excel_template(cls) -> io.BytesIO:
        """Generate formatted Excel (.xlsx) import template with styled header row."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Student Import Template"

        headers = ["Name", "Register Number", "Department", "Email", "Marks", "Year"]
        ws.append(headers)

        header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
        header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")

        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        sample_rows = [
            ["Rahul Nair", "REG2024101", "Computer Science", "rahul.nair@example.edu", 86.5, 1],
            ["Pooja Sundaram", "REG2024102", "Information Technology", "pooja.s@example.edu", 91.0, 2],
            ["Manoj Kumar", "REG2024103", "Data Science", "manoj.k@example.edu", 74.0, 1],
        ]
        for row in sample_rows:
            ws.append(row)

        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = openpyxl.utils.get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 4, 15)

        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        return buffer

    @classmethod
    def parse_and_validate_file(cls, file_stream, filename: str) -> dict[str, Any]:
        """
        Parse uploaded CSV or Excel file and validate row-by-row.
        Returns a structured dictionary with preview rows and summary statistics.
        """
        fname_lower = filename.lower()
        if hasattr(file_stream, 'read'):
            raw_bytes = file_stream.read()
        else:
            raw_bytes = file_stream

        rows_raw: list[dict[str, Any]] = []

        if fname_lower.endswith(('.xlsx', '.xlsm', '.xltx')):
            try:
                wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True)
                ws = wb.active
                iter_rows = list(ws.iter_rows(values_only=True))
                if not iter_rows:
                    return {"success": False, "error": "Uploaded Excel workbook is empty.", "rows": [], "summary": {}}
                
                header_row = [str(c).strip().lower() if c is not None else "" for c in iter_rows[0]]
                for r_idx, r_vals in enumerate(iter_rows[1:], start=2):
                    if not any(r_vals):
                        continue
                    row_dict = {}
                    for h_idx, h_name in enumerate(header_row):
                        if h_name and h_idx < len(r_vals):
                            row_dict[h_name] = r_vals[h_idx]
                    row_dict["_row_number"] = r_idx
                    rows_raw.append(row_dict)
            except Exception as e:
                return {"success": False, "error": f"Failed to parse Excel workbook: {e!s}", "rows": [], "summary": {}}
        elif fname_lower.endswith('.csv'):
            try:
                if isinstance(raw_bytes, bytes):
                    text = raw_bytes.decode('utf-8-sig', errors='replace')
                else:
                    text = str(raw_bytes)
                if not text.strip():
                    return {"success": False, "error": "Uploaded CSV file is empty.", "rows": [], "summary": {}}

                reader = csv.DictReader(io.StringIO(text))
                if not reader.fieldnames:
                    return {"success": False, "error": "Missing column headers in CSV file.", "rows": [], "summary": {}}

                for r_idx, row in enumerate(reader, start=2):
                    clean_row = {k.strip().lower(): v for k, v in row.items() if k}
                    clean_row["_row_number"] = r_idx
                    rows_raw.append(clean_row)
            except Exception as e:
                return {"success": False, "error": f"Failed to read CSV file: {e!s}", "rows": [], "summary": {}}
        else:
            return {"success": False, "error": "Unsupported file format. Please upload a .csv or .xlsx file.", "rows": [], "summary": {}}

        if not rows_raw:
            return {"success": False, "error": "No data rows found in uploaded file.", "rows": [], "summary": {}}

        # Check required headers
        first_row_keys = set(rows_raw[0].keys()) - {"_row_number"}
        # Map lenient column names
        field_map = cls._map_headers(first_row_keys)
        if not field_map.get('name') or not field_map.get('register_number') or not field_map.get('department') or not field_map.get('email') or not field_map.get('marks'):
            missing = []
            if not field_map.get('name'):
                missing.append('Name')
            if not field_map.get('register_number'):
                missing.append('Register Number')
            if not field_map.get('department'):
                missing.append('Department')
            if not field_map.get('email'):
                missing.append('Email')
            if not field_map.get('marks'):
                missing.append('Marks')
            return {"success": False, "error": f"Missing required columns in file: {', '.join(missing)}", "rows": [], "summary": {}}

        preview_rows = []
        valid_count = 0
        error_count = 0

        seen_reg_nos = set()
        seen_emails = set()

        # Preload existing registers and emails from DB for collision check
        existing_regs = {r['register_number'].upper() for r in query_db("SELECT register_number FROM students")}
        existing_emails = {r['email'].lower() for r in query_db("SELECT email FROM students")}

        for r in rows_raw:
            r_num = r.get("_row_number", 0)
            raw_name = str(r.get(field_map['name'], '') or '').strip()
            raw_reg = str(r.get(field_map['register_number'], '') or '').strip()
            raw_dept = str(r.get(field_map['department'], '') or '').strip()
            raw_email = str(r.get(field_map['email'], '') or '').strip()
            raw_marks = str(r.get(field_map['marks'], '') or '').strip()
            raw_year = str((field_map.get('year') and r.get(field_map['year'])) or '1').strip()

            row_errors = []

            # Clean and validate basic types
            is_valid, errors, cleaned = validate_student_data(
                name=raw_name,
                register_number=raw_reg,
                department=raw_dept,
                email=raw_email,
                marks_raw=raw_marks,
                year=raw_year
            )

            if not is_valid:
                row_errors.extend(errors)

            clean_reg = (cleaned.get('register_number') if is_valid else raw_reg).strip().upper()
            clean_email = (cleaned.get('email') if is_valid else raw_email).strip().lower()

            # Duplicate check within file
            if clean_reg:
                if clean_reg in seen_reg_nos:
                    row_errors.append(f"Duplicate register number '{clean_reg}' within uploaded file.")
                seen_reg_nos.add(clean_reg)

            if clean_email:
                if clean_email in seen_emails:
                    row_errors.append(f"Duplicate email '{clean_email}' within uploaded file.")
                seen_emails.add(clean_email)

            # Duplicate check against database
            if clean_reg and clean_reg in existing_regs:
                row_errors.append(f"Register number '{clean_reg}' is already registered in the system.")
            if clean_email and clean_email in existing_emails:
                row_errors.append(f"Email '{clean_email}' is already registered in the system.")

            try:
                marks_val = float(cleaned.get('marks', raw_marks) if is_valid else (float(raw_marks) if raw_marks else 0.0))
            except (ValueError, TypeError):
                marks_val = 0.0

            grade_letter, grade_point = get_grade_and_points(marks_val)

            status = "valid" if not row_errors else "error"
            if status == "valid":
                valid_count += 1
            else:
                error_count += 1

            preview_rows.append({
                "row_number": r_num,
                "name": raw_name,
                "register_number": clean_reg or raw_reg,
                "department": raw_dept,
                "year": cleaned.get('year', 1) if is_valid else 1,
                "email": clean_email or raw_email,
                "marks": marks_val,
                "grade": grade_letter,
                "grade_point": grade_point,
                "status": status,
                "errors": row_errors,
                "cleaned": cleaned if is_valid and not row_errors else None
            })

        return {
            "success": True,
            "error": None,
            "rows": preview_rows,
            "summary": {
                "total": len(preview_rows),
                "valid": valid_count,
                "error": error_count
            }
        }

    @classmethod
    def _map_headers(cls, headers: set[str]) -> dict[str, str]:
        """Map flexible header variations to canonical fields."""
        mapping = {}
        for h in headers:
            norm = h.strip().lower().replace('_', ' ').replace('-', ' ')
            if norm in ('name', 'student name', 'full name'):
                mapping['name'] = h
            elif norm in ('register number', 'reg no', 'reg number', 'registration number', 'register_number'):
                mapping['register_number'] = h
            elif norm in ('department', 'dept', 'branch'):
                mapping['department'] = h
            elif norm in ('year', 'academic year', 'curr year'):
                mapping['year'] = h
            elif norm in ('email', 'email address', 'mail'):
                mapping['email'] = h
            elif norm in ('marks', 'score', 'percentage', 'total marks'):
                mapping['marks'] = h
        return mapping

    @classmethod
    def commit_valid_import(cls, valid_cleaned_rows: list[dict[str, Any]], created_by_user_id: int) -> tuple[int, int, list[str]]:
        """
        Persist validated student records into database in atomic transactions.
        Returns: (inserted_count, skipped_count, error_messages)
        """
        inserted_count = 0
        skipped_count = 0
        errors = []

        for item in valid_cleaned_rows:
            try:
                sid, student, err = StudentService.create_student(
                    cleaned=item,
                    photo_filename=None,
                    created_by_user_id=created_by_user_id
                )
                if sid:
                    inserted_count += 1
                else:
                    skipped_count += 1
                    errors.append(f"Student {item.get('register_number')}: {err}")
            except Exception as e:
                skipped_count += 1
                errors.append(f"Student {item.get('register_number')}: {e!s}")

        return inserted_count, skipped_count, errors

    @classmethod
    def process_csv_import(cls, csv_file_stream, created_by_user_id: int) -> tuple[int, list[str], str]:
        """Backward-compatible bulk CSV processor."""
        parsed = cls.parse_and_validate_file(csv_file_stream, "upload.csv")
        if not parsed.get("success"):
            return 0, [parsed.get("error", "Failed to parse CSV")], ""

        valid_items = [r["cleaned"] for r in parsed["rows"] if r["status"] == "valid" and r.get("cleaned")]
        inserted, skipped, errs = cls.commit_valid_import(valid_items, created_by_user_id)

        all_skipped = [f"Row {r['row_number']} ({r['register_number']}): {'; '.join(r['errors'])}" for r in parsed["rows"] if r["status"] == "error"]
        all_skipped.extend(errs)

        return inserted, all_skipped, ""

    @classmethod
    def import_students_csv(cls, csv_content, created_by_user_id: int) -> tuple[int, list[str], str]:
        """
        Import students from raw CSV bytes or string with validation report.
        Returns: (inserted_count, errors_list, report_string)
        """
        if isinstance(csv_content, (bytes, bytearray)):
            stream = io.BytesIO(csv_content)
        elif isinstance(csv_content, str):
            stream = io.BytesIO(csv_content.encode('utf-8'))
        else:
            stream = csv_content

        parsed = cls.parse_and_validate_file(stream, "import.csv")
        if not parsed.get("success"):
            err_msg = parsed.get("error") or "Uploaded file is empty or invalid"
            return 0, [err_msg], json.dumps(parsed)

        valid_rows = [r["cleaned"] for r in parsed["rows"] if r["status"] == "valid" and r.get("cleaned")]
        error_rows = [r for r in parsed["rows"] if r["status"] == "error"]

        inserted_count, skipped_count, commit_errors = cls.commit_valid_import(valid_rows, created_by_user_id)

        errors_list = []
        for r in error_rows:
            row_num = r.get("row_number", "?")
            msg = f"Row {row_num}: " + "; ".join(r.get("errors", ["Validation failed"]))
            errors_list.append(msg)
        errors_list.extend(commit_errors)

        report_summary = json.dumps({
            "total_rows": len(parsed["rows"]),
            "inserted": inserted_count,
            "errors": len(errors_list),
            "rows": [{"row_number": r.get("row_number"), "errors": r.get("errors")} for r in error_rows]
        })

        return inserted_count, errors_list, report_summary
