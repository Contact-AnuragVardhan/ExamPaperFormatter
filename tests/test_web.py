"""Browser wrapper checks. The page only calls the existing pipeline."""

from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

from docx import Document
from docx.enum.text import WD_UNDERLINE

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from format.word_formatter import format_exam
from run_core import run_pipeline
from web.app import create_app

TEACHER = ROOT / "input" / "Teacher Exam English 10 Actual.docx"
REFERENCE = ROOT / "input" / "Reference Exam English 10 WORD.docx"
FIXTURE_HASH = hashlib.sha256(TEACHER.read_bytes()).hexdigest()


def _runs(path: Path):
    doc = Document(str(path))
    rows = []
    for para in doc.paragraphs:
        runs = []
        for run in para.runs:
            if not run.text and not run._r.xpath(".//*[local-name()='blip']"):
                continue
            underlined = run.underline not in (None, False, WD_UNDERLINE.NONE)
            runs.append((run.text, run.bold is True, underlined))
        rows.append((para.text, runs))
    return rows


def _image_hash(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        media = [name for name in zf.namelist() if name.startswith("word/media/")]
        assert len(media) == 1
        return hashlib.sha256(zf.read(media[0])).hexdigest()


def test_browser_workflow(tmp_path):
    data = tmp_path / "data"
    app = create_app(data)
    client = app.test_client()

    missing = client.get("/")
    assert b"No Reference Exam loaded." in missing.data
    blocked = client.post(
        "/format",
        data={"teacher": (TEACHER.open("rb"), TEACHER.name)},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"No Reference Exam loaded." in blocked.data

    rejected = client.post(
        "/reference",
        data={"reference": (b"not a docx", "notes.txt")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Upload a DOCX reference exam." in rejected.data

    uploaded = client.post(
        "/reference",
        data={"reference": (REFERENCE.open("rb"), REFERENCE.name)},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Reference Exam English 10 WORD.docx" in uploaded.data
    assert hashlib.sha256(TEACHER.read_bytes()).hexdigest() == FIXTURE_HASH

    direct_dir = tmp_path / "direct"
    direct_dir.mkdir()
    direct = run_pipeline(TEACHER, REFERENCE, direct_dir)
    assert direct["ok"], direct["problems"]
    direct_docx = direct_dir / "direct.docx"
    format_exam(direct["blocks"], TEACHER, direct_docx, REFERENCE)

    formatted = client.post(
        "/format",
        data={"teacher": (TEACHER.open("rb"), TEACHER.name)},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"DOWNLOAD FORMATTED EXAM" in formatted.data
    assert b"Teacher Exam English 10 Actual.docx" in formatted.data
    assert b"Teacher Exam English 10 Actual_FORMATTED.docx" in formatted.data

    page = formatted.data.decode("utf-8")
    start = page.find("/exams/")
    exam_id = page[start + len("/exams/") : page.find("/download", start)]
    download = client.get(f"/exams/{exam_id}/download")
    assert download.status_code == 200
    browser_docx = tmp_path / "browser.docx"
    browser_docx.write_bytes(download.data)
    download.close()

    assert _runs(direct_docx) == _runs(browser_docx)
    assert _image_hash(direct_docx) == _image_hash(browser_docx)
    assert _image_hash(browser_docx) == hashlib.sha256(
        zipfile.ZipFile(TEACHER).read("word/media/image1.jpg")
    ).hexdigest()

    removed_exam = client.post(f"/exams/{exam_id}/delete", follow_redirects=True)
    assert b"No formatted exams yet." in removed_exam.data
    assert not (data / "exams" / exam_id).exists()

    restarted = create_app(data).test_client()
    still_there = restarted.get("/")
    assert b"Reference Exam English 10 WORD.docx" in still_there.data

    client.post("/reference/delete", follow_redirects=True)
    cleared = client.get("/")
    assert b"No Reference Exam loaded." in cleared.data
    assert REFERENCE.exists()
    assert (ROOT / "input" / "Teacher Exam English 10 Actual.docx").exists()
