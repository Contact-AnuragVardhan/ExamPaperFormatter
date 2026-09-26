"""WhatsApp webhook transport tests. No network calls are made."""

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

    def send_text(self, to, text, phone_number_id=None):
        self.texts.append((to, text, phone_number_id))

    def download_document(self, media_id):
        self.downloaded.append(media_id)
        return b"fake-docx-for-transport-test"


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

    response = client.post(
        "/webhook",
        json=_payload({"id": "wamid-3", "from": "15551234567", "type": "text", "text": {"body": "English"}}),
    )
    assert response.status_code == 200
    assert "exam name/type" in fake.texts[-1][1].lower()
