"""Local page that uploads a reference and a teacher exam, then calls the existing pipeline."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from flask import Flask, redirect, render_template, request, send_file, url_for
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from format.format_integrity import check_format
from format.nonconformance import find_nonconformances, write_nonconformance_file
from format.word_formatter import format_exam
from run_core import run_pipeline
from web.references import ReferenceLibrary, display_text
from web.whatsapp import register_whatsapp_routes


LOG_LEVEL = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
    force=True,
)
LOGGER = logging.getLogger(__name__)


def create_app(data_root: Path | None = None) -> Flask:
    app = Flask(__name__)
    configured_data_root = os.getenv("EXAM_DATA_ROOT", "").strip()
    if data_root is not None:
        root = Path(data_root)
    elif configured_data_root:
        root = Path(configured_data_root)
    else:
        root = ROOT / "data"
    root.mkdir(parents=True, exist_ok=True)
    app.config["DATA_ROOT"] = root

    def references_dir() -> Path:
        path = root / "references"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def exams_dir() -> Path:
        path = root / "exams"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def reference_path() -> Path:
        return references_dir() / "active.docx"

    def index_path() -> Path:
        return exams_dir() / "index.json"

    def load_index() -> list[dict]:
        path = index_path()
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def save_index(rows: list[dict]) -> None:
        index_path().write_text(json.dumps(rows, indent=2), encoding="utf-8")

    library = ReferenceLibrary(references_dir())

    def is_docx(filename: str) -> bool:
        return bool(filename) and filename.lower().endswith(".docx")

    def download_stem(filename: str) -> str:
        stem = Path(filename).stem
        cleaned = re.sub(r"[^\w .\-]", "", stem).strip()
        return cleaned or "exam"

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            references=library.listed(),
            exams=list(reversed(load_index())),
            message=request.args.get("message", ""),
            error=request.args.get("error", ""),
            download_id=request.args.get("download", ""),
        )

    @app.post("/reference")
    def upload_reference():
        grade = request.form.get("grade", "")
        subject = request.form.get("subject", "")
        if not display_text(grade) or not display_text(subject):
            return redirect(url_for("index", error="Enter a grade and a subject."))
        upload = request.files.get("reference")
        if upload is None or not is_docx(upload.filename or ""):
            return redirect(url_for("index", error="Upload a DOCX reference exam."))
        try:
            error = library.add(grade, subject, upload.filename or "reference.docx", upload.read())
        except Exception:
            LOGGER.exception("Reference upload failed")
            return redirect(url_for("index", error="Reference exam could not be saved."))
        if error:
            return redirect(url_for("index", error=error))
        return redirect(url_for("index", message="Reference exam saved."))

    @app.post("/reference/delete")
    def delete_reference():
        grade = request.form.get("grade", "")
        subject = request.form.get("subject", "")
        if library.remove(grade, subject):
            return redirect(url_for("index", message="Reference exam removed."))
        return redirect(url_for("index", error="That Reference Exam is not stored."))

    @app.post("/format")
    def format_teacher():
        grade = request.form.get("grade", "")
        subject = request.form.get("subject", "")
        if not display_text(grade) or not display_text(subject):
            return redirect(url_for("index", error="Enter a grade and a subject."))
        selected = library.find(grade, subject)
        if selected is None:
            return redirect(
                url_for(
                    "index",
                    error=f"No Reference Exam found for Grade {display_text(grade)} / {display_text(subject)}.",
                )
            )
        selected_reference = library.file_path(selected)
        upload = request.files.get("teacher")
        if upload is None or not is_docx(upload.filename or ""):
            return redirect(url_for("index", error="Upload a DOCX teacher exam."))
        original_name = Path(upload.filename).name
        exam_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
        folder = exams_dir() / exam_id
        folder.mkdir(parents=True)
        original = folder / "original.docx"
        formatted = folder / "formatted.docx"
        upload.save(original)
        LOGGER.info(
            "[browser:%s] Upload received: filename=%s bytes=%d",
            exam_id,
            original_name,
            original.stat().st_size,
        )
        try:
            LOGGER.info("[browser:%s] Reference conformance check started", exam_id)
            errors = find_nonconformances(original, selected_reference)
            if errors:
                LOGGER.info(
                    "[browser:%s] Reference conformance failed with %d issue(s); creating corrections DOCX",
                    exam_id,
                    len(errors),
                )
                write_nonconformance_file(original, formatted, errors, selected_reference)
                formatted_name = download_stem(original_name) + "_CORRECTIONS.docx"
                message = (
                    "This exam does not match the Reference Exam. "
                    "Download the file, follow the yellow ERROR/FIX notes, delete those notes, and upload again."
                )
            else:
                LOGGER.info("[browser:%s] Reference conformance passed; formatting started", exam_id)
                result = run_pipeline(
                    original,
                    selected_reference,
                    folder / "work",
                    log_context=f"browser:{exam_id}",
                )
                if not result["ok"]:
                    LOGGER.error("Browser exam %s failed Phase 1: %s", exam_id, result.get("problems"))
                    shutil.rmtree(folder, ignore_errors=True)
                    return redirect(url_for("index", error="Formatting failed. The exam was not saved."))
                LOGGER.info("[browser:%s] DOCX rendering started", exam_id)
                format_exam(result["blocks"], original, formatted, selected_reference)
                LOGGER.info(
                    "[browser:%s] DOCX rendering complete: bytes=%d",
                    exam_id,
                    formatted.stat().st_size if formatted.exists() else 0,
                )
                LOGGER.info("[browser:%s] Format integrity check started", exam_id)
                problems = check_format(result["blocks"], original, formatted, selected_reference)
                if problems:
                    LOGGER.error("[browser:%s] Format integrity failed: %s", exam_id, problems)
                    shutil.rmtree(folder, ignore_errors=True)
                    return redirect(url_for("index", error="Formatting failed. The exam was not saved."))
                LOGGER.info("[browser:%s] Format integrity check passed", exam_id)
                formatted_name = download_stem(original_name) + "_FORMATTED.docx"
                message = "Exam formatted."
        except Exception:
            LOGGER.exception("Browser exam %s formatting failed", exam_id)
            shutil.rmtree(folder, ignore_errors=True)
            return redirect(url_for("index", error="Formatting failed. The exam was not saved."))
        rows = load_index()
        rows.append(
            {
                "id": exam_id,
                "original_filename": original_name,
                "formatted_filename": formatted_name,
                "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        )
        save_index(rows)
        LOGGER.info("[browser:%s] Browser processing completed: output=%s", exam_id, formatted_name)
        return redirect(url_for("index", message=message, download=exam_id))

    @app.get("/exams/<exam_id>/download")
    def download_exam(exam_id: str):
        row = next((item for item in load_index() if item["id"] == exam_id), None)
        path = exams_dir() / exam_id / "formatted.docx"
        if row is None or not path.exists():
            return redirect(url_for("index", error="That formatted exam is not available."))
        return send_file(path, as_attachment=True, download_name=row["formatted_filename"])

    @app.post("/exams/<exam_id>/delete")
    def delete_exam(exam_id: str):
        rows = [item for item in load_index() if item["id"] != exam_id]
        save_index(rows)
        shutil.rmtree(exams_dir() / exam_id, ignore_errors=True)
        return redirect(url_for("index", message="Formatted exam removed."))

    register_whatsapp_routes(
        app,
        data_root=root,
        reference_path=reference_path,
        exams_dir=exams_dir,
        load_index=load_index,
        save_index=save_index,
        download_stem=download_stem,
    )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=False,
    )
