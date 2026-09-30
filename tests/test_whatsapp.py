from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from web.app import create_app


class FakeWhatsAppClient:
    def __init__(self):
        self.texts = []
        self.downloaded = []
        self.documents = []

    def send_text(self, to, text, phone_number_id=None):
        self.texts.append((to, text, phone_number_id))

    def download_document(self, media_id):
        self.downloaded.append(media_id)
        return b"fake-docx-for-transport-test"

    def send_document(self, to, path, filename, caption, phone_number_id=None):
        self.documents.append((to, Path(path), filename, caption, phone_number_id))


def _payload(message):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "12345"},
                            "contacts": [{"profile": {"name": "Teacher"}}],
                            "messages": [message],
                        }
                    }
                ]
            }
        ],
    }


def test_webhook_verification(tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify-me")
    app = create_app(tmp_path / "data")
    client = app.test_client()

    ok = client.get("/webhook?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=abc123")
    assert ok.status_code == 200
    assert ok.data == b"abc123"

    bad = client.get("/webhook?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=abc123")
    assert bad.status_code == 403


def test_document_then_metadata_questions(tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_ASYNC", "false")
    app = create_app(tmp_path / "data")
    bot = app.config["WHATSAPP_BOT"]
    fake = FakeWhatsAppClient()
    bot.client = fake

    # The WhatsApp layer intentionally reuses the active browser reference.
    reference = tmp_path / "data" / "references" / "active.docx"
    reference.parent.mkdir(parents=True, exist_ok=True)
    reference.write_bytes(b"placeholder-reference")

    client = app.test_client()
    response = client.post(
        "/webhook",
        json=_payload(
            {
                "id": "wamid-1",
                "from": "15551234567",
                "type": "document",
                "document": {
                    "id": "media-1",
                    "filename": "Teacher Exam.docx",
                    "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                },
            }
        ),
    )
    assert response.status_code == 200
    assert fake.downloaded == ["media-1"]
    assert "class/grade" in fake.texts[-1][1]

    response = client.post(
        "/webhook",
        json=_payload({"id": "wamid-2", "from": "15551234567", "type": "text", "text": {"body": "10"}}),
    )
    assert response.status_code == 200
    assert "subject" in fake.texts[-1][1].lower()

    formatted_sessions = []
    bot._format_and_send = lambda sender, session: formatted_sessions.append((sender, dict(session)))

    response = client.post(
        "/webhook",
        json=_payload({"id": "wamid-3", "from": "15551234567", "type": "text", "text": {"body": "English"}}),
    )
    assert response.status_code == 200
    assert "checking the exam against the reference" in fake.texts[-1][1].lower()
    assert len(formatted_sessions) == 1
    assert formatted_sessions[0][1]["metadata"] == {"class_grade": "10", "subject": "English"}


def test_nonconforming_exam_sends_corrections_docx(tmp_path, monkeypatch):
    from docx import Document
    from docx.enum.text import WD_COLOR_INDEX

    monkeypatch.setenv("WHATSAPP_ASYNC", "false")
    app = create_app(tmp_path / "data")
    bot = app.config["WHATSAPP_BOT"]
    fake = FakeWhatsAppClient()
    bot.client = fake

    reference_source = ROOT / "input" / "Reference Exam English 10 WORD.docx"
    bad_source = ROOT / "input" / "Teacher Exam English 10 BAD.docx"
    reference = tmp_path / "data" / "references" / "active.docx"
    reference.parent.mkdir(parents=True, exist_ok=True)
    reference.write_bytes(reference_source.read_bytes())

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    original = incoming_dir / "original.docx"
    original.write_bytes(bad_source.read_bytes())

    session = {
        "state": "formatting",
        "incoming_dir": str(incoming_dir),
        "original_path": str(original),
        "original_filename": bad_source.name,
        "profile_name": "Teacher",
        "phone_number_id": "12345",
        "metadata": {"class_grade": "10", "subject": "English"},
    }
    bot.store.set("15551234567", session)

    bot._format_and_send("15551234567", session)

    assert len(fake.documents) == 1
    to, sent_path, filename, caption, phone_number_id = fake.documents[0]
    assert to == "15551234567"
    assert phone_number_id == "12345"
    assert filename == "Teacher Exam English 10 BAD_CORRECTIONS.docx"
    assert "does not match the Reference Exam" in caption
    assert "yellow ERROR/FIX notes" in caption
    assert "formatted exam is ready" not in caption.lower()

    # The bot clears the temporary incoming folder after sending, but the saved exam copy remains.
    rows = bot.load_index()
    assert len(rows) == 1
    assert rows[0]["formatted_filename"] == filename
    saved = tmp_path / "data" / "exams" / rows[0]["id"] / "formatted.docx"
    assert saved.exists()

    doc = Document(str(saved))
    notes = [p for p in doc.paragraphs if p.text.startswith("ERROR:") or p.text.startswith("FIX:")]
    assert len(notes) == 4
    assert any("Class header is missing." in p.text for p in notes)
    assert any("Section C is missing." in p.text for p in notes)
    for paragraph in notes:
        runs = [run for run in paragraph.runs if run.text]
        assert runs
        assert all(run.font.highlight_color == WD_COLOR_INDEX.YELLOW for run in runs)
