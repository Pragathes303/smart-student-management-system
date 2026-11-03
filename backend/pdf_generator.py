"""
PDF Marksheet & Grade Report Generator
Generates institutional, official academic grade sheets for students using ReportLab.
Includes Subject-Wise Semester Breakdown, Credits, CGPA, and Attendance metrics.
"""

import io
import os
from datetime import datetime

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from backend.utils import calculate_gpa, calculate_grade, get_attendance_status


def generate_student_marksheet_pdf(student, subjects=None, attendance=None, verification_url=None):
    """
    Generate an official, professional academic grade card PDF with digital verification QR code.
    
    Args:
        student (dict): Student record dictionary from database.
        subjects (list): Optional list of student_subjects dictionaries.
        attendance (dict): Optional student_attendance dictionary.
        verification_url (str): Optional verification URL to encode in QR code.
        
    Returns:
        io.BytesIO: In-memory binary PDF buffer.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=18,
        leading=22,
        textColor=colors.HexColor('#1e3a8a'),
        alignment=1
    )
    
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=13,
        textColor=colors.HexColor('#475569'),
        alignment=1
    )

    section_heading = ParagraphStyle(
        'SectionHeading',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=14,
        textColor=colors.HexColor('#0f172a'),
        spaceBefore=10,
        spaceAfter=5
    )

    cell_bold = ParagraphStyle(
        'CellBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#1e293b')
    )

    cell_normal = ParagraphStyle(
        'CellNormal',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#334155')
    )

    cell_center = ParagraphStyle(
        'CellCenter',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12,
        alignment=1,
        textColor=colors.HexColor('#0f172a')
    )

    cell_header = ParagraphStyle(
        'CellHdr',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        alignment=1,
        textColor=colors.white
    )

    story = []

    # 1. Header Banner
    story.append(Paragraph("INSTITUTE OF HIGHER EDUCATION &amp; TECHNOLOGY", title_style))
    story.append(Spacer(1, 3))
    story.append(Paragraph("OFFICE OF THE ACADEMIC REGISTRAR &bull; CONSOLIDATED GRADE SHEET", subtitle_style))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#1e3a8a'), spaceAfter=10))

    # 2. Student Information Table
    story.append(Paragraph("STUDENT IDENTIFICATION", section_heading))
    
    att_pct = attendance.get('attendance_percentage', 85.0) if attendance else 85.0
    att_info = get_attendance_status(att_pct)

    student_info_data = [
        [
            Paragraph("Student Name:", cell_bold),
            Paragraph(student.get('name', 'N/A'), cell_normal),
            Paragraph("Register Number:", cell_bold),
            Paragraph(student.get('register_number', 'N/A'), cell_bold)
        ],
        [
            Paragraph("Department:", cell_bold),
            Paragraph(student.get('department', 'N/A'), cell_normal),
            Paragraph("Email Address:", cell_bold),
            Paragraph(student.get('email', 'N/A'), cell_normal)
        ],
        [
            Paragraph("Cumulative GPA:", cell_bold),
            Paragraph(f"<strong>{student.get('cgpa') or '8.40'} / 10.00</strong>", cell_normal),
            Paragraph("Attendance Status:", cell_bold),
            Paragraph(f"{att_pct}% ({att_info['status']})", cell_normal)
        ]
    ]

    info_table = Table(student_info_data, colWidths=[110, 160, 120, 150])
    info_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#cbd5e1')),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 10))

    # 3. Semester-Wise Subjects Table (if available) or Cumulative Summary
    if subjects and len(subjects) > 0:
        story.append(Paragraph("SEMESTER-WISE SUBJECT EVALUATIONS &amp; CREDITS", section_heading))
        
        subj_table_data = [
            [
                Paragraph("Sem", cell_header),
                Paragraph("Code", cell_header),
                Paragraph("Subject Title", ParagraphStyle('HdrL', parent=cell_header, alignment=0)),
                Paragraph("Credits", cell_header),
                Paragraph("Marks", cell_header),
                Paragraph("Grade", cell_header),
                Paragraph("Grade Pt", cell_header)
            ]
        ]

        for s in subjects:
            subj_table_data.append([
                Paragraph(f"S{s.get('semester', 1)}", cell_center),
                Paragraph(s.get('subject_code', ''), cell_center),
                Paragraph(s.get('subject_name', ''), cell_normal),
                Paragraph(str(s.get('credits', 3)), cell_center),
                Paragraph(f"{float(s.get('marks', 0)):.1f}", cell_center),
                Paragraph(s.get('letter_grade', 'B'), cell_center),
                Paragraph(f"{float(s.get('grade_point', 0)):.1f}", cell_center),
            ])

        gpa_stats = calculate_gpa(subjects)

        # Append GPA Summary Row
        subj_table_data.append([
            Paragraph("<strong>TOTALS / GPA</strong>", cell_bold),
            Paragraph("", cell_normal),
            Paragraph(f"Completed Credits: {gpa_stats['passed_credits']} / {gpa_stats['total_credits']}", cell_bold),
            Paragraph(str(gpa_stats['total_credits']), cell_center),
            Paragraph("", cell_center),
            Paragraph("SGPA/CGPA:", cell_bold),
            Paragraph(f"<strong>{gpa_stats['gpa']}</strong>", cell_center),
        ])

        subj_table = Table(subj_table_data, colWidths=[40, 60, 220, 50, 55, 55, 60])
        subj_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e3a8a')),
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#cbd5e1')),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f1f5f9')),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(subj_table)
    else:
        # Standard Single Evaluation Card
        story.append(Paragraph("ACADEMIC PERFORMANCE SUMMARY", section_heading))
        grade_info = calculate_grade(student.get('marks', 0))
        status_label = "PASSED" if grade_info['passed'] else "FAILED / REAPPEAR"
        status_color = colors.HexColor('#15803d') if grade_info['passed'] else colors.HexColor('#b91c1c')

        academic_data = [
            [
                Paragraph("Assessment Module", cell_bold),
                Paragraph("Max Marks", cell_bold),
                Paragraph("Marks Scored", cell_bold),
                Paragraph("Letter Grade", cell_bold),
                Paragraph("Classification", cell_bold),
                Paragraph("Result", cell_bold)
            ],
            [
                Paragraph("Cumulative Academic Evaluation", cell_normal),
                Paragraph("100.00", cell_normal),
                Paragraph(f"{float(student.get('marks', 0)):.2f}", cell_center),
                Paragraph(grade_info['grade'], cell_center),
                Paragraph(grade_info['label'], cell_normal),
                Paragraph(status_label, ParagraphStyle('Res', fontName='Helvetica-Bold', fontSize=9, textColor=status_color))
            ]
        ]

        academic_table = Table(academic_data, colWidths=[160, 65, 80, 75, 85, 75])
        academic_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e3a8a')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#cbd5e1')),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(academic_table)

    story.append(Spacer(1, 12))

    # 4. Grading Scheme Reference Key
    story.append(Paragraph("GRADING SCALE REFERENCE", section_heading))
    scale_data = [
        [
            Paragraph("<strong>O (90-100)</strong>: Outstanding (10.0)", cell_normal),
            Paragraph("<strong>A+ (80-89)</strong>: Excellent (9.0)", cell_normal),
            Paragraph("<strong>A (70-79)</strong>: Very Good (8.0)", cell_normal),
        ],
        [
            Paragraph("<strong>B+ (60-69)</strong>: Good (7.0)", cell_normal),
            Paragraph("<strong>B (50-59)</strong>: Pass (6.0)", cell_normal),
            Paragraph("<strong>RA (&lt;50)</strong>: Re-Appear (0.0)", cell_normal),
        ]
    ]
    scale_table = Table(scale_data, colWidths=[180, 180, 180])
    scale_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(scale_table)
    story.append(Spacer(1, 28))

    # 5. Institutional Sign-Off Block with Live Verification QR Code
    reg_no = student.get('register_number', 'STUDENT')
    if not verification_url:
        base = os.getenv('APP_BASE_URL', os.getenv('BASE_URL', 'http://127.0.0.1:5000'))
        vtok = student.get('verify_token')
        if vtok:
            verification_url = f"{base.rstrip('/')}/verify/{vtok}"
        else:
            verification_url = f"{base.rstrip('/')}/verify/{reg_no}"

    # Generate vector QR Code for online document verification
    qr_widget = QrCodeWidget(verification_url)
    bounds = qr_widget.getBounds()
    w = bounds[2] - bounds[0]
    h = bounds[3] - bounds[1]
    qr_drawing = Drawing(60, 60, transform=[60.0/w, 0, 0, 60.0/h, 0, 0])
    qr_drawing.add(qr_widget)

    qr_cell = [
        qr_drawing,
        Spacer(1, 2),
        Paragraph("<font size=7 color='#64748b'>Scan to verify authenticity</font>", cell_normal)
    ]

    sig_data = [
        [
            qr_cell,
            Paragraph(
                "<em>Official System Generated Document</em><br/>"
                f"Register No: <strong>{reg_no}</strong><br/>"
                f"Date of Issue: {datetime.now().strftime('%d-%b-%Y')}<br/><br/>"
                "__________________________________<br/>"
                "<strong>Controller of Examinations</strong>",
                cell_bold
            )
        ]
    ]
    sig_table = Table(sig_data, colWidths=[200, 340])
    sig_table.setStyle(TableStyle([
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(sig_table)

    # Build PDF Document
    doc.build(story)
    buffer.seek(0)
    return buffer
