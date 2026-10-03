"""Browser reference catalog: one Reference DOCX per grade and subject."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from web.app import create_app
from web.references import ReferenceLibrary

ENGLISH = ROOT / "input" / "Reference Exam English 10 WORD.docx"
HISTORY = ROOT / "tests" / "fixtures" / "synthetic_reference.docx"


def _upload(client, grade, subject, path: Path):
    return client.post(
        "/reference",
        data={
            "grade": grade,
            "subject": subject,
            "reference": (path.open("rb"), path.name),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def test_add_view_duplicate_and_lookup(tmp_path):
    app = create_app(tmp_path / "data")
    client = app.test_client()

    english = _upload(client, "10", "English", ENGLISH)
    assert b"Reference Exam English 10 WORD.docx" in english.data
    assert b"Reference already exists" not in english.data

    history = _upload(client, "7", "History", HISTORY)
    page = history.data.decode("utf-8")
    assert "synthetic_reference.docx" in page
    assert ">10<" in page and ">English<" in page
    assert ">7<" in page and ">History<" in page

    duplicate = _upload(client, "10", " ENGLISH ", ENGLISH)
    assert b"Reference already exists for Grade 10 / English." in duplicate.data
    listed = client.get("/").data.decode("utf-8")
    assert listed.count("Reference Exam English 10 WORD.docx") == 1

    for subject in ("english", "English", " ENGLISH "):
        looked = client.post(
            "/format",
            data={"grade": " 10 ", "subject": subject, "teacher": (b"not-a-docx", "notes.txt")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        body = looked.data.decode("utf-8")
        assert "No Reference Exam found" not in body
        assert "Upload a DOCX teacher exam." in body

    missing = client.post(
        "/format",
        data={
            "grade": "8",
            "subject": "Science",
            "teacher": (HISTORY.open("rb"), HISTORY.name),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"No Reference Exam found for Grade 8 / Science." in missing.data
    exams = tmp_path / "data" / "exams"
    assert not exams.exists() or not any(exams.iterdir())


def test_legacy_english_reference_becomes_grade_10(tmp_path):
    references = tmp_path / "references"
    references.mkdir()
    shutil.copyfile(ENGLISH, references / "active.docx")
    (references / "active.json").write_text(
        json.dumps({"original_filename": ENGLISH.name}),
        encoding="utf-8",
    )
    library = ReferenceLibrary(references)
    row = library.find("10", "english")
    assert row is not None
    assert row["grade"] == "10"
    assert row["subject"] == "English"
    assert row["filename"] == ENGLISH.name
    assert (references / "active.docx").exists()
