"""Browser catalog of Reference Exams, one per grade and subject.

The formatter still receives one Reference DOCX. This module only stores
that file and chooses it from the grade and subject entered on the page.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from reference_profile import write_profile
from reference_profile.sections import PROFILE_DIR

# The previously saved single reference, identified by the name already
# stored beside it. Any other saved file is left alone.
_LEGACY_ENGLISH_NAME = "Reference Exam English 10 WORD.docx"


def display_text(value: str) -> str:
    return " ".join((value or "").split())


def reference_key(grade: str, subject: str) -> tuple[str, str]:
    return (display_text(grade).casefold(), display_text(subject).casefold())


class ReferenceLibrary:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.files = self.directory / "files"
        self.files.mkdir(parents=True, exist_ok=True)
        self.catalog_path = self.directory / "catalog.json"
        self.rows = self._load()
        self._migrate_legacy_english()

    def _load(self) -> list[dict]:
        if not self.catalog_path.exists():
            return []
        try:
            data = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []

    def _save(self) -> None:
        self.catalog_path.write_text(
            json.dumps(self.rows, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def listed(self) -> list[dict]:
        return sorted(self.rows, key=lambda row: (row["grade_key"], row["subject_key"], row["filename"]))

    def find(self, grade: str, subject: str) -> dict | None:
        key = reference_key(grade, subject)
        if not key[0] or not key[1]:
            return None
        for row in self.rows:
            if (row.get("grade_key"), row.get("subject_key")) == key:
                return row
        return None

    def file_path(self, row: dict) -> Path:
        return self.directory / row["stored_path"]

    def add(self, grade: str, subject: str, filename: str, data: bytes) -> str | None:
        """Store one reference. Return an error message, or None on success."""
        shown_grade = display_text(grade)
        shown_subject = display_text(subject)
        if not shown_grade or not shown_subject:
            return "Enter a grade and a subject."
        existing = self.find(shown_grade, shown_subject)
        if existing:
            return (
                f"Reference already exists for Grade {existing['grade']} / {existing['subject']}."
            )
        key = reference_key(shown_grade, shown_subject)
        digest = hashlib.sha256(f"{key[0]}\n{key[1]}".encode("utf-8")).hexdigest()[:16]
        stored_path = Path("files") / f"{digest}.docx"
        target = self.directory / stored_path
        target.write_bytes(data)
        try:
            write_profile(target, PROFILE_DIR)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        self.rows.append(
            {
                "grade": shown_grade,
                "subject": shown_subject,
                "grade_key": key[0],
                "subject_key": key[1],
                "filename": Path(filename).name,
                "stored_path": stored_path.as_posix(),
                "source_hash": {
                    "algorithm": "sha256",
                    "hex": hashlib.sha256(data).hexdigest(),
                },
            }
        )
        self._save()
        return None

    def remove(self, grade: str, subject: str) -> bool:
        row = self.find(grade, subject)
        if row is None:
            return False
        self.file_path(row).unlink(missing_ok=True)
        self.rows = [item for item in self.rows if item is not row]
        self._save()
        return True

    def _migrate_legacy_english(self) -> None:
        if self.rows:
            return
        active = self.directory / "active.docx"
        meta_path = self.directory / "active.json"
        if not active.is_file() or not meta_path.is_file():
            return
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if meta.get("original_filename") != _LEGACY_ENGLISH_NAME:
            return
        raw = active.read_bytes()
        known = Path(__file__).resolve().parents[1] / "input" / _LEGACY_ENGLISH_NAME
        if known.is_file() and hashlib.sha256(known.read_bytes()).hexdigest() != hashlib.sha256(raw).hexdigest():
            return
        self.add("10", "English", _LEGACY_ENGLISH_NAME, raw)
