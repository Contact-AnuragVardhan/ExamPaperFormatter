"""Presentation constants taken from the reference exam's visible style."""

from docx.shared import Inches, Pt, Twips

PAGE_WIDTH = Inches(8.5)
PAGE_HEIGHT = Inches(11)
MARGIN = Inches(0.75)

FONT_NAME = "Calibri"
BODY_SIZE = Pt(11)
MAJOR_SIZE = Pt(12)
SECTION_SIZE = Pt(13)
SCHOOL_SIZE = Pt(15)
EXAM_TITLE_SIZE = Pt(14)

# Reference indents: 432 twips ≈ 0.3 in, 1296 twips ≈ 0.9 in, syllabus 160 twips.
INDENT_STEP = Inches(0.3)
INDENT_CHOICE = Inches(0.9)
INDENT_SYLLABUS = Twips(160)
MAX_IMAGE_WIDTH = Inches(6.5)

SPACE_MAJOR_BEFORE = Pt(10)   # 200 twips
SPACE_MAJOR_AFTER = Pt(5)     # 100 twips
SPACE_SECTION_BEFORE = Pt(12)  # 240 twips
SPACE_SECTION_AFTER = Pt(2)    # 40 twips
SPACE_PASSAGE_BEFORE = Pt(6)   # 120 twips
SPACE_PASSAGE_AFTER = Pt(6)
SPACE_SUB_BEFORE = Pt(5)       # 100 twips
SPACE_SUB_AFTER = Pt(3)        # 60 twips
SPACE_CHOICE_BEFORE = Pt(0)
SPACE_CHOICE_AFTER = Pt(1.5)   # 30 twips
SPACE_HEADER_SCHOOL_AFTER = Pt(2)   # 40 twips
SPACE_HEADER_EXAM_AFTER = Pt(6)     # 120 twips
SPACE_HEADER_LAST_AFTER = Pt(10)    # 200 twips
SPACE_SYLLABUS_HEAD_BEFORE = Pt(4)  # 80 twips
SPACE_SYLLABUS_HEAD_AFTER = Pt(4)
SPACE_OR_BEFORE = Pt(6)
SPACE_OR_AFTER = Pt(6)
