"""
Unit Tests for Centralized Grading System (Anna University 10-Point Scale)
Validates B11 requirements:
- Grade boundaries: O (90-100), A+ (80-89), A (70-79), B+ (60-69), B (50-59), RA (<50)
- Grade points: 10.0, 9.0, 8.0, 7.0, 6.0, 0.0
- CGPA calculation & weighted credits
- Arrears detection & academic standing
"""

import pytest
from backend.grading import (
    get_grade_info,
    get_grade_and_points,
    calculate_grade,
    calculate_cgpa,
    calculate_sgpa,
    calculate_semester_and_overall_gpa
)


def test_anna_university_grade_boundaries():
    """Verify exact score tier mappings on Anna University 10-point scale."""
    # O Grade: 90 - 100 (10.0)
    o_max = get_grade_info(100.0)
    assert o_max["letter"] == "O"
    assert o_max["grade_point"] == 10.0
    assert o_max["is_pass"] is True

    o_min = get_grade_info(90.0)
    assert o_min["letter"] == "O"
    assert o_min["grade_point"] == 10.0

    # A+ Grade: 80 - 89.99 (9.0)
    aplus_high = get_grade_info(89.9)
    assert aplus_high["letter"] == "A+"
    assert aplus_high["grade_point"] == 9.0

    aplus_min = get_grade_info(80.0)
    assert aplus_min["letter"] == "A+"
    assert aplus_min["grade_point"] == 9.0

    # A Grade: 70 - 79.99 (8.0)
    a_high = get_grade_info(79.9)
    assert a_high["letter"] == "A"
    assert a_high["grade_point"] == 8.0

    a_min = get_grade_info(70.0)
    assert a_min["letter"] == "A"
    assert a_min["grade_point"] == 8.0

    # B+ Grade: 60 - 69.99 (7.0)
    bplus_high = get_grade_info(69.9)
    assert bplus_high["letter"] == "B+"
    assert bplus_high["grade_point"] == 7.0

    bplus_min = get_grade_info(60.0)
    assert bplus_min["letter"] == "B+"
    assert bplus_min["grade_point"] == 7.0

    # B Grade: 50 - 59.99 (6.0)
    b_high = get_grade_info(59.9)
    assert b_high["letter"] == "B"
    assert b_high["grade_point"] == 6.0

    b_min = get_grade_info(50.0)
    assert b_min["letter"] == "B"
    assert b_min["grade_point"] == 6.0

    # RA Grade: < 50 (0.0, Fail)
    ra_high = get_grade_info(49.9)
    assert ra_high["letter"] == "RA"
    assert ra_high["grade_point"] == 0.0
    assert ra_high["is_pass"] is False

    ra_zero = get_grade_info(0.0)
    assert ra_zero["letter"] == "RA"
    assert ra_zero["grade_point"] == 0.0
    assert ra_zero["is_pass"] is False


def test_reusable_get_grade_and_points_boundary_values():
    """
    Phase 3: Unit test single reusable grade function with specified boundary values:
    49, 50, 59, 60, 89, 90, 100.
    """
    # 49 -> RA, 0.0 (Fail)
    letter, gp = get_grade_and_points(49)
    assert letter == "RA"
    assert gp == 0.0

    # 50 -> B, 6.0 (Pass threshold)
    letter, gp = get_grade_and_points(50)
    assert letter == "B"
    assert gp == 6.0

    # 59 -> B, 6.0 (Top of B tier)
    letter, gp = get_grade_and_points(59)
    assert letter == "B"
    assert gp == 6.0

    # 60 -> B+, 7.0 (Start of B+ tier)
    letter, gp = get_grade_and_points(60)
    assert letter == "B+"
    assert gp == 7.0

    # 89 -> A+, 9.0 (Top of A+ tier)
    letter, gp = get_grade_and_points(89)
    assert letter == "A+"
    assert gp == 9.0

    # 90 -> O, 10.0 (Start of O tier)
    letter, gp = get_grade_and_points(90)
    assert letter == "O"
    assert gp == 10.0

    # 100 -> O, 10.0 (Max score)
    letter, gp = get_grade_and_points(100)
    assert letter == "O"
    assert gp == 10.0


def test_grade_clamping_and_invalid_inputs():
    """Verify out-of-range and invalid inputs are safely clamped and handled."""
    assert get_grade_info(150.0)["letter"] == "O"
    assert get_grade_info(-20.0)["letter"] == "RA"
    assert get_grade_info("not-a-number")["letter"] == "RA"
    assert get_grade_info(None)["letter"] == "RA"

    assert get_grade_and_points(120) == ("O", 10.0)
    assert get_grade_and_points(-10) == ("RA", 0.0)
    assert get_grade_and_points("invalid") == ("RA", 0.0)


def test_cgpa_calculation_clean_record():
    """Verify CGPA calculation on students without arrears."""
    subjects = [
        {"subject_code": "CS101", "credits": 4, "grade_point": 10.0},  # 4 * 10 = 40
        {"subject_code": "CS102", "credits": 3, "grade_point": 9.0},   # 3 * 9 = 27
        {"subject_code": "CS103", "credits": 3, "grade_point": 8.0},   # 3 * 8 = 24
    ]
    # Total Credits: 10, Total Points: 91.0 -> CGPA = 9.10
    result = calculate_cgpa(subjects)
    assert result["total_credits"] == 10
    assert result["earned_credits"] == 10
    assert result["weighted_points"] == 91.0
    assert result["cgpa"] == 9.10
    assert result["has_arrears"] is False
    assert result["arrears_count"] == 0
    assert result["standing"] == "First Class with Distinction"


def test_cgpa_calculation_with_arrears():
    """Verify CGPA calculation correctly detects arrears and adjusts standing."""
    subjects = [
        {"subject_code": "CS101", "credits": 4, "grade_point": 9.0},   # 36
        {"subject_code": "CS102", "credits": 3, "grade_point": 0.0},   # 0 (RA)
    ]
    # Total Credits: 7, Earned: 4, Points: 36.0 -> CGPA = 36/7 = 5.14
    result = calculate_cgpa(subjects)
    assert result["total_credits"] == 7
    assert result["earned_credits"] == 4
    assert result["cgpa"] == 5.14
    assert result["has_arrears"] is True
    assert result["arrears_count"] == 1
    assert "Arrears Pending" in result["standing"]


def test_cgpa_empty_subjects():
    """Verify empty subjects returns zero-initialized summary."""
    result = calculate_cgpa([])
    assert result["total_credits"] == 0
    assert result["cgpa"] == 0.0
    assert result["has_arrears"] is False


def test_semester_sgpa_and_cumulative_cgpa():
    """
    Phase 3: Verify SGPA per semester and CGPA overall across multiple semesters,
    credit-weighted.
    """
    subjects = [
        # Semester 1: 4*10 + 3*9 = 67; 7 credits -> SGPA = 9.57
        {"semester": 1, "subject_code": "CS101", "credits": 4, "marks": 95, "grade_point": 10.0},
        {"semester": 1, "subject_code": "CS102", "credits": 3, "marks": 85, "grade_point": 9.0},
        # Semester 2: 3*8 + 4*7 = 52; 7 credits -> SGPA = 7.43
        {"semester": 2, "subject_code": "CS201", "credits": 3, "marks": 75, "grade_point": 8.0},
        {"semester": 2, "subject_code": "CS202", "credits": 4, "marks": 65, "grade_point": 7.0},
    ]

    calc = calculate_semester_and_overall_gpa(subjects)

    # Semester 1 SGPA
    sem1 = calc["by_semester"][1]
    assert sem1["total_credits"] == 7
    assert sem1["sgpa"] == 9.57

    # Semester 2 SGPA
    sem2 = calc["by_semester"][2]
    assert sem2["total_credits"] == 7
    assert sem2["sgpa"] == 7.43

    # Overall CGPA: (67 + 52) / 14 = 119 / 14 = 8.50
    overall = calc["overall"]
    assert overall["total_credits"] == 14
    assert overall["cgpa"] == 8.50
    assert overall["has_arrears"] is False

