"""Teacher exams that follow the synthetic History reference.

These are fixtures. They are not runtime policy.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document

DIRECTORY = Path(__file__).resolve().parent
GOOD = DIRECTORY / "history_teacher_good.docx"
BAD = DIRECTORY / "history_teacher_bad.docx"

GOOD_LINES = [
    "Northwind Academy",
    "Term Assessment",
    "Class: 7",
    "Subject: History",
    "Part I",
    "1. Name a river civilization.",
    "a. State where its people lived.",
    "b. State one food they farmed.",
    "2. Give one reason towns built walls.",
    "Part II",
    "Surplus grain supported town trade.",
    "3. Describe one town market.",
    "Part III",
    "4. Define a primary source in one sentence.",
]

BAD_LINES = [
    "Northwind Academy",
    "Term Assessment",
    "Class: 7",
    "Part I",
    "1. Name a river civilization.",
    "Part III",
    "4. Define a primary source in one sentence.",
]


def _write(path: Path, lines: list[str]) -> Path:
    doc = Document()
    for line in lines:
        doc.add_paragraph(line)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


def write_good(path: Path = GOOD) -> Path:
    return _write(path, GOOD_LINES)


def write_bad(path: Path = BAD) -> Path:
    return _write(path, BAD_LINES)


if __name__ == "__main__":
    print(write_good())
    print(write_bad())
