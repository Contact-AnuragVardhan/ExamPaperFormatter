"""Spacing the Reference Profile does not record as its own role.

Syllabus-item indent, passage spacing, alternative-marker spacing, and
the image-width cap stay here. Heading text and the alternative marker
come from the selected profile.
"""

from docx.shared import Inches, Pt, Twips

INDENT_SYLLABUS = Twips(160)
MAX_IMAGE_WIDTH = Inches(6.5)

SPACE_PASSAGE_BEFORE = Pt(6)
SPACE_PASSAGE_AFTER = Pt(6)
SPACE_SYLLABUS_HEAD_BEFORE = Pt(4)
SPACE_SYLLABUS_HEAD_AFTER = Pt(4)
SPACE_OR_BEFORE = Pt(6)
SPACE_OR_AFTER = Pt(6)
