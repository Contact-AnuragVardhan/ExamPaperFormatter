"""Write the small non-English reference used to test profile discovery."""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.shared import Pt, Twips

DESTINATION = Path(__file__).resolve().parent / "synthetic_reference.docx"


def _add(doc: Document, text: str, size: int, bold: bool = False):
    paragraph = doc.add_paragraph()
    run = paragraph.add_run(text)
    run.font.name = "Times New Roman"
    run.font.size = Pt(size)
    run.bold = bold
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(6)
    return paragraph


def write_synthetic_reference(path: Path = DESTINATION) -> Path:
    doc = Document()
    section = doc.sections[0]
    section.page_width = Twips(11906)
    section.page_height = Twips(16838)
    section.left_margin = Twips(720)
    section.right_margin = Twips(720)
    section.top_margin = Twips(720)
    section.bottom_margin = Twips(720)

    _add(doc, "Northwind Academy", 18, bold=True)
    _add(doc, "Term Assessment", 14, bold=True)
    _add(doc, "Class: 7", 12)
    _add(doc, "Subject: History", 12)
    _add(doc, "Part I", 16, bold=True)
    _add(doc, "1. Name one river-valley civilization.", 14, bold=True)
    _add(doc, "a. Mention where people settled.", 12)
    _add(doc, "b. Mention one crop they grew.", 12)
    _add(doc, "2. Why did cities need walls?", 14, bold=True)
    _add(doc, "Part II", 16, bold=True)
    _add(doc, "Cities grew where water and farmland were reliable.", 12)
    _add(doc, "3. Describe a marketplace.", 14, bold=True)
    _add(doc, "Part III", 16, bold=True)
    _add(doc, "4. What is a primary source?", 14, bold=True)

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


if __name__ == "__main__":
    print(write_synthetic_reference())
