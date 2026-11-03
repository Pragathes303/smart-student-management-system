"""
Centralized Academic Grading System
Anna University 10-Point Scale & CGPA Calculator

Grade Scale:
  90 - 100: O  (Outstanding, Grade Point: 10.0, Pass)
  80 -  89: A+ (Excellent,   Grade Point: 9.0,  Pass)
  70 -  79: A  (Very Good,   Grade Point: 8.0,  Pass)
  60 -  69: B+ (Good,        Grade Point: 7.0,  Pass)
  50 -  59: B  (Average,     Grade Point: 6.0,  Pass)
   0 -  49: RA (Reappear,    Grade Point: 0.0,  Fail)
"""

from typing import Any

GRADE_SCALE = [
    {
        "min_marks": 90.0,
        "max_marks": 100.0,
        "letter": "O",
        "points": 10.0,
        "description": "Outstanding",
        "status": "Pass",
        "css_class": "badge-grade-o"
    },
    {
        "min_marks": 80.0,
        "max_marks": 89.99,
        "letter": "A+",
        "points": 9.0,
        "description": "Excellent",
        "status": "Pass",
        "css_class": "badge-grade-aplus"
    },
    {
        "min_marks": 70.0,
        "max_marks": 79.99,
        "letter": "A",
        "points": 8.0,
        "description": "Very Good",
        "status": "Pass",
        "css_class": "badge-grade-a"
    },
    {
        "min_marks": 60.0,
        "max_marks": 69.99,
        "letter": "B+",
        "points": 7.0,
        "description": "Good",
        "status": "Pass",
        "css_class": "badge-grade-bplus"
    },
    {
        "min_marks": 50.0,
        "max_marks": 59.99,
        "letter": "B",
        "points": 6.0,
        "description": "Above Average",
        "status": "Pass",
        "css_class": "badge-grade-b"
    },
    {
        "min_marks": 0.0,
        "max_marks": 49.99,
        "letter": "RA",
        "points": 0.0,
        "description": "Reappear",
        "status": "Fail",
        "css_class": "badge-grade-f"
    }
]


def get_grade_info(marks: float) -> dict[str, Any]:
    """
    Get full grade information for a given numeric mark (0-100) based on
    Anna University 10-point scale.
    """
    try:
        val = float(marks)
    except (TypeError, ValueError):
        val = 0.0

    # Clamp marks to valid range [0, 100]
    val = max(0.0, min(100.0, val))

    for tier in GRADE_SCALE:
        if val >= tier["min_marks"]:
            return {
                "letter": tier["letter"],
                "grade": tier["letter"],       # Alias for backward compatibility
                "grade_point": tier["points"],
                "points": tier["points"],
                "description": tier["description"],
                "status": tier["status"],
                "css_class": tier["css_class"],
                "is_pass": tier["status"] == "Pass"
            }

    # Fallback to RA (Reappear)
    return {
        "letter": "RA",
        "grade": "RA",
        "grade_point": 0.0,
        "points": 0.0,
        "description": "Reappear",
        "status": "Fail",
        "css_class": "badge-grade-f",
        "is_pass": False
    }


def get_grade_and_points(marks: float) -> tuple[str, float]:
    """
    Single reusable grade function: marks -> (grade, grade_point).
    Anna University 10-point scale:
      90 - 100: ('O', 10.0)
      80 -  89: ('A+', 9.0)
      70 -  79: ('A', 8.0)
      60 -  69: ('B+', 7.0)
      50 -  59: ('B', 6.0)
       0 -  49: ('RA', 0.0)

    Returns:
        tuple[str, float]: (grade_letter, grade_point)
    """
    info = get_grade_info(marks)
    return info["letter"], info["grade_point"]


def calculate_grade(marks: float) -> dict[str, Any]:
    """
    Backward-compatible helper function for legacy callers expecting
    dict with 'grade', 'points', 'description', 'status'.
    Also provides Anna University grade letters (O, A+, A, B+, B, RA).
    """
    return get_grade_info(marks)


def calculate_sgpa(subjects: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Calculate Semester Grade Point Average (SGPA) for subjects in a given semester.
    Formula: SGPA = Sum(Credits * Grade_Point) / Sum(Credits)
    """
    res = calculate_cgpa(subjects)
    res["sgpa"] = res["cgpa"]
    return res


def calculate_semester_and_overall_gpa(subjects: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Group subjects by semester, calculate credit-weighted SGPA per semester,
    and calculate cumulative CGPA overall across all semesters.
    """
    if not subjects:
        return {
            "by_semester": {},
            "overall": calculate_cgpa([])
        }

    grouped: dict[int, list[dict[str, Any]]] = {}
    for sub in subjects:
        try:
            sem = int(sub.get("semester", 1))
        except (ValueError, TypeError):
            sem = 1
        if sem not in grouped:
            grouped[sem] = []
        grouped[sem].append(sub)

    by_semester = {}
    for sem in sorted(grouped.keys()):
        sem_subs = grouped[sem]
        sem_stats = calculate_cgpa(sem_subs)
        sem_stats["semester"] = sem
        sem_stats["sgpa"] = sem_stats["cgpa"]
        sem_stats["subjects"] = sem_subs
        by_semester[sem] = sem_stats

    overall = calculate_cgpa(subjects)
    return {
        "by_semester": by_semester,
        "overall": overall
    }


def calculate_cgpa(subjects: list[dict[str, Any]]) -> dict[str, Any]:

    """
    Calculate Cumulative Grade Point Average (CGPA) from a list of subjects.
    Each subject must contain 'credits' and 'grade_point' or 'marks'.

    Formula: CGPA = Sum(Credits * Grade_Point) / Sum(Credits)
    """
    if not subjects:
        return {
            "total_credits": 0,
            "earned_credits": 0,
            "passed_credits": 0,
            "weighted_points": 0.0,
            "gpa": 0.0,
            "cgpa": 0.0,
            "standing": "No Subjects Registered",
            "has_arrears": False,
            "arrears": 0,
            "arrears_count": 0,
            "arrear_count": 0
        }

    total_credits = 0
    earned_credits = 0
    weighted_points = 0.0
    arrears_count = 0

    for subj in subjects:
        try:
            credits = int(subj.get("credits", 0))
        except (ValueError, TypeError):
            credits = 0

        # If grade_point is given directly, use it; otherwise compute from marks
        if "grade_point" in subj and subj["grade_point"] is not None:
            try:
                gp = float(subj["grade_point"])
            except (ValueError, TypeError):
                gp = 0.0
        elif "marks" in subj and subj["marks"] is not None:
            grade_data = get_grade_info(subj["marks"])
            gp = grade_data["grade_point"]
        else:
            gp = 0.0

        total_credits += credits

        if gp > 0.0:
            earned_credits += credits
            weighted_points += credits * gp
        else:
            arrears_count += 1

    cgpa = round(weighted_points / total_credits, 2) if total_credits > 0 else 0.0

    # Determine Academic Standing
    if arrears_count > 0:
        standing = f"Arrears Pending ({arrears_count} Subject{'s' if arrears_count > 1 else ''})"
    elif cgpa >= 8.5:
        standing = "First Class with Distinction"
    elif cgpa >= 6.5:
        standing = "First Class"
    elif cgpa >= 5.0:
        standing = "Second Class"
    else:
        standing = "At Risk"

    return {
        "total_credits": total_credits,
        "earned_credits": earned_credits,
        "passed_credits": earned_credits,
        "weighted_points": round(weighted_points, 2),
        "gpa": cgpa,
        "cgpa": cgpa,
        "standing": standing,
        "has_arrears": arrears_count > 0,
        "arrears": arrears_count,
        "arrears_count": arrears_count,
        "arrear_count": arrears_count
    }


def format_cgpa_report(subjects: list[dict[str, Any]]) -> str:
    """Format a readable academic CGPA summary report for a list of subjects."""
    stats = calculate_cgpa(subjects)
    lines = [
        f"CGPA: {stats['cgpa']} | Total Credits: {stats['total_credits']} | Standing: {stats['standing']}",
        "Subjects:"
    ]
    for s in subjects:
        name = s.get("subject_name", s.get("name", "Unknown Subject"))
        gp = s.get("grade_point", "-")
        marks = s.get("marks", "-")
        letter = s.get("letter_grade", s.get("letter", "-"))
        lines.append(f" - {name}: Marks={marks}, Grade={letter}, GP={gp}")
    return "\n".join(lines)
