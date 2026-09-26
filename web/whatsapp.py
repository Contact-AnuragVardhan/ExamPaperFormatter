"""WhatsApp Cloud API adapter for the existing exam formatter.

This module intentionally does not change the formatter itself.  It only adds a
transport layer that can receive a teacher DOCX, collect a few workflow metadata
fields in chat, call the existing pipeline, and send the formatted DOCX back.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import mimetypes
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

import requests
from flask import Flask, Response, request
from werkzeug.utils import secure_filename

from format.format_integrity import check_format
from format.word_formatter import format_exam
from run_core import run_pipeline

LOGGER = logging.getLogger(__name__)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DEFAULT_GRAPH_VERSION = "v25.0"

# These fields are collected for the WhatsApp workflow and saved with the exam.
# The existing formatter does not use them yet; it still formats from the DOCX
# plus the active reference exam exactly as the browser flow does.
METADATA_FIELDS = [
    ("class_grade", "What class/grade is this exam for? (Example: 10)"),
    ("subject", "What subject is this exam for? (Example: English)"),
    ("exam_name", "What is the exam name/type? (Example: Quarterly Exam)"),
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_sender(sender: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]", "_", sender) or "unknown"


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


class JsonSessionStore:
    """Very small JSON-backed session store suitable for the current POC."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.sessions_path = self.root / "sessions.json"
        self.seen_path = self.root / "seen_message_ids.json"
        self._lock = threading.RLock()

    @staticmethod
    def _read_json(path: Path, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    @staticmethod
    def _write_json(path: Path, value) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
        temporary.replace(path)

    def get(self, sender: str) -> dict | None:
        with self._lock:
            sessions = self._read_json(self.sessions_path, {})
            value = sessions.get(sender)
            return dict(value) if isinstance(value, dict) else None

    def set(self, sender: str, session: dict) -> None:
        with self._lock:
            sessions = self._read_json(self.sessions_path, {})
            sessions[sender] = session
            self._write_json(self.sessions_path, sessions)

    def clear(self, sender: str) -> dict | None:
        with self._lock:
            sessions = self._read_json(self.sessions_path, {})
            previous = sessions.pop(sender, None)
            self._write_json(self.sessions_path, sessions)
            return previous

    def mark_seen(self, message_id: str) -> bool:
        """Return False for duplicate webhook deliveries."""
        if not message_id:
            return True
        with self._lock:
            data = self._read_json(self.seen_path, {"ids": []})
            ids = list(data.get("ids") or [])
            if message_id in ids:
                return False
            ids.append(message_id)
            self._write_json(self.seen_path, {"ids": ids[-500:]})
            return True


class WhatsAppClient:
    def __init__(self) -> None:
        self.graph_version = os.getenv("WHATSAPP_GRAPH_VERSION", DEFAULT_GRAPH_VERSION).strip()
        self.access_token = (os.getenv("WHATSAPP_TOKEN") or os.getenv("WHATSAPP_ACCESS_TOKEN", "")).strip()
        self.default_phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
        self.timeout = int(os.getenv("WHATSAPP_HTTP_TIMEOUT", "60"))

    def _headers(self) -> dict[str, str]:
        if not self.access_token:
            raise RuntimeError("WHATSAPP_TOKEN is not configured.")
        return {"Authorization": f"Bearer {self.access_token}"}

    def _phone_number_id(self, value: str | None) -> str:
        phone_number_id = (value or self.default_phone_number_id).strip()
        if not phone_number_id:
            raise RuntimeError("WHATSAPP_PHONE_NUMBER_ID is not configured or present in the webhook payload.")
        return phone_number_id

    def send_text(self, to: str, text: str, phone_number_id: str | None = None) -> None:
        phone_id = self._phone_number_id(phone_number_id)
        url = f"https://graph.facebook.com/{self.graph_version}/{phone_id}/messages"
        response = requests.post(
            url,
            headers={**self._headers(), "Content-Type": "application/json"},
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"body": text},
            },
            timeout=self.timeout,
        )
        response.raise_for_status()

    def download_document(self, media_id: str) -> bytes:
        info_url = f"https://graph.facebook.com/{self.graph_version}/{media_id}"
        info = requests.get(info_url, headers=self._headers(), timeout=self.timeout)
        info.raise_for_status()
        media_url = info.json().get("url")
        if not media_url:
            raise RuntimeError("WhatsApp media lookup did not return a download URL.")
        media = requests.get(media_url, headers=self._headers(), timeout=self.timeout)
        media.raise_for_status()
        return media.content

    def upload_document(self, path: Path, phone_number_id: str | None = None) -> str:
        phone_id = self._phone_number_id(phone_number_id)
        url = f"https://graph.facebook.com/{self.graph_version}/{phone_id}/media"
        mime_type = mimetypes.guess_type(path.name)[0] or DOCX_MIME
        with path.open("rb") as handle:
            response = requests.post(
                url,
                headers=self._headers(),
                data={"messaging_product": "whatsapp", "type": mime_type},
                files={"file": (path.name, handle, mime_type)},
                timeout=self.timeout,
            )
        response.raise_for_status()
        media_id = response.json().get("id")
        if not media_id:
            raise RuntimeError("WhatsApp media upload did not return a media id.")
        return media_id

    def send_document(
        self,
        to: str,
        path: Path,
        filename: str,
        caption: str,
        phone_number_id: str | None = None,
    ) -> None:
        phone_id = self._phone_number_id(phone_number_id)
        media_id = self.upload_document(path, phone_id)
        url = f"https://graph.facebook.com/{self.graph_version}/{phone_id}/messages"
        response = requests.post(
            url,
            headers={**self._headers(), "Content-Type": "application/json"},
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "document",
                "document": {
                    "id": media_id,
                    "filename": filename,
                    "caption": caption,
                },
            },
            timeout=self.timeout,
        )
        response.raise_for_status()


class WhatsAppExamBot:
    def __init__(
        self,
        data_root: Path,
        reference_path: Callable[[], Path],
        exams_dir: Callable[[], Path],
        load_index: Callable[[], list[dict]],
        save_index: Callable[[list[dict]], None],
        download_stem: Callable[[str], str],
        client: WhatsAppClient | None = None,
    ) -> None:
        self.data_root = Path(data_root)
        self.whatsapp_root = self.data_root / "whatsapp"
        self.incoming_root = self.whatsapp_root / "incoming"
        self.incoming_root.mkdir(parents=True, exist_ok=True)
        self.store = JsonSessionStore(self.whatsapp_root)
        self.client = client or WhatsAppClient()
        self.reference_path = reference_path
        self.exams_dir = exams_dir
        self.load_index = load_index
        self.save_index = save_index
        self.download_stem = download_stem

    def handle_payload(self, payload: dict) -> None:
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                phone_number_id = ((value.get("metadata") or {}).get("phone_number_id") or "").strip()
                contacts = value.get("contacts") or []
                profile_name = ""
                if contacts:
                    profile_name = (((contacts[0] or {}).get("profile") or {}).get("name") or "").strip()
                for message in value.get("messages") or []:
                    message_id = str(message.get("id") or "")
                    if not self.store.mark_seen(message_id):
                        continue
                    sender = str(message.get("from") or "").strip()
                    if not sender:
                        continue
                    try:
                        self._handle_message(sender, phone_number_id, profile_name, message)
                    except Exception:
                        LOGGER.exception("WhatsApp message processing failed for message %s", message_id)
                        try:
                            self.client.send_text(
                                sender,
                                "Something went wrong while processing your exam. Please try again or contact the administrator.",
                                phone_number_id,
                            )
                        except Exception:
                            LOGGER.exception("Could not send WhatsApp failure message")

    def _handle_message(self, sender: str, phone_number_id: str, profile_name: str, message: dict) -> None:
        message_type = str(message.get("type") or "")
        if message_type == "document":
            self._handle_document(sender, phone_number_id, profile_name, message.get("document") or {})
            return
        if message_type == "text":
            text = str(((message.get("text") or {}).get("body")) or "").strip()
            self._handle_text(sender, phone_number_id, text)
            return
        self.client.send_text(
            sender,
            "Please send the unformatted exam as a Word .docx document.",
            phone_number_id,
        )

    def _clear_session_files(self, session: dict | None) -> None:
        if not session:
            return
        incoming_dir = session.get("incoming_dir")
        if incoming_dir:
            shutil.rmtree(incoming_dir, ignore_errors=True)

    def _handle_document(self, sender: str, phone_number_id: str, profile_name: str, document: dict) -> None:
        filename = secure_filename(str(document.get("filename") or "exam.docx")) or "exam.docx"
        mime_type = str(document.get("mime_type") or "").lower()
        media_id = str(document.get("id") or "").strip()
        is_docx = filename.lower().endswith(".docx") or mime_type == DOCX_MIME
        if not is_docx:
            self.client.send_text(sender, "Please send a Word .docx file. Other document types are not supported yet.", phone_number_id)
            return
        if not media_id:
            self.client.send_text(sender, "I could not read that WhatsApp document. Please send the .docx file again.", phone_number_id)
            return
        if not self.reference_path().exists():
            self.client.send_text(
                sender,
                "The formatter is not ready because no active reference exam is configured. Please ask the administrator to upload the reference exam first.",
                phone_number_id,
            )
            return

        old_session = self.store.clear(sender)
        self._clear_session_files(old_session)

        incoming_dir = self.incoming_root / _safe_sender(sender) / uuid4().hex
        incoming_dir.mkdir(parents=True, exist_ok=True)
        original_path = incoming_dir / "original.docx"
        original_path.write_bytes(self.client.download_document(media_id))

        session = {
            "state": "awaiting_metadata",
            "incoming_dir": str(incoming_dir),
            "original_path": str(original_path),
            "original_filename": filename,
            "profile_name": profile_name,
            "phone_number_id": phone_number_id,
            "metadata": {},
            "metadata_index": 0,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }
        self.store.set(sender, session)
        self.client.send_text(
            sender,
            f"I received {filename}. {METADATA_FIELDS[0][1]}",
            phone_number_id,
        )

    def _handle_text(self, sender: str, phone_number_id: str, text: str) -> None:
        if text.lower() in {"cancel", "stop", "reset"}:
            session = self.store.clear(sender)
            self._clear_session_files(session)
            self.client.send_text(sender, "Cancelled. Send a new unformatted .docx exam whenever you are ready.", phone_number_id)
            return

        session = self.store.get(sender)
        if not session or session.get("state") != "awaiting_metadata":
            self.client.send_text(
                sender,
                "Send the unformatted exam as a Word .docx document. I will then ask for the remaining exam details.",
                phone_number_id,
            )
            return

        if not text:
            index = int(session.get("metadata_index", 0))
            self.client.send_text(sender, METADATA_FIELDS[index][1], phone_number_id)
            return

        index = int(session.get("metadata_index", 0))
        if index >= len(METADATA_FIELDS):
            return
        key, _prompt = METADATA_FIELDS[index]
        metadata = dict(session.get("metadata") or {})
        metadata[key] = text
        index += 1
        session["metadata"] = metadata
        session["metadata_index"] = index
        session["phone_number_id"] = phone_number_id or session.get("phone_number_id", "")
        session["updated_at"] = _utc_now()

        if index < len(METADATA_FIELDS):
            self.store.set(sender, session)
            self.client.send_text(sender, METADATA_FIELDS[index][1], phone_number_id)
            return

        session["state"] = "formatting"
        self.store.set(sender, session)
        self.client.send_text(sender, "Thanks. I have the details. I am formatting the exam now.", phone_number_id)
        self._format_and_send(sender, session)

    def _format_and_send(self, sender: str, session: dict) -> None:
        phone_number_id = str(session.get("phone_number_id") or "")
        original_filename = str(session.get("original_filename") or "exam.docx")
        source = Path(str(session["original_path"]))
        exam_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
        folder = self.exams_dir() / exam_id
        folder.mkdir(parents=True, exist_ok=False)
        original = folder / "original.docx"
        formatted = folder / "formatted.docx"
        shutil.copy2(source, original)

        try:
            result = run_pipeline(original, self.reference_path(), folder / "work")
            if not result["ok"]:
                LOGGER.error("WhatsApp exam %s failed Phase 1: %s", exam_id, result.get("problems"))
                raise RuntimeError("Exam hierarchy validation failed.")
            format_exam(result["blocks"], original, formatted)
            problems = check_format(result["blocks"], original, formatted, self.reference_path())
            if problems:
                LOGGER.error("WhatsApp exam %s failed format integrity: %s", exam_id, problems)
                raise RuntimeError("Formatted exam integrity validation failed.")

            formatted_name = self.download_stem(original_filename) + "_FORMATTED.docx"
            metadata_record = {
                "source": "whatsapp",
                "original_filename": original_filename,
                "profile_name": session.get("profile_name", ""),
                "metadata": session.get("metadata", {}),
                "created_at": _utc_now(),
            }
            (folder / "metadata.json").write_text(json.dumps(metadata_record, indent=2), encoding="utf-8")

            rows = self.load_index()
            rows.append(
                {
                    "id": exam_id,
                    "original_filename": original_filename,
                    "formatted_filename": formatted_name,
                    "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "source": "whatsapp",
                    "metadata": session.get("metadata", {}),
                }
            )
            self.save_index(rows)
            self.client.send_document(
                sender,
                formatted,
                formatted_name,
                "Your formatted exam is ready.",
                phone_number_id,
            )
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        finally:
            completed = self.store.clear(sender)
            self._clear_session_files(completed)


def _verify_signature(raw_body: bytes, signature: str, app_secret: str) -> bool:
    if not signature.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.removeprefix("sha256="), expected)


def register_whatsapp_routes(
    app: Flask,
    data_root: Path,
    reference_path: Callable[[], Path],
    exams_dir: Callable[[], Path],
    load_index: Callable[[], list[dict]],
    save_index: Callable[[list[dict]], None],
    download_stem: Callable[[str], str],
) -> WhatsAppExamBot:
    bot = WhatsAppExamBot(
        data_root=data_root,
        reference_path=reference_path,
        exams_dir=exams_dir,
        load_index=load_index,
        save_index=save_index,
        download_stem=download_stem,
    )
    app.config["WHATSAPP_BOT"] = bot

    @app.get("/webhook")
    def whatsapp_webhook_verify():
        verify_token = (os.getenv("VERIFY_TOKEN") or os.getenv("WHATSAPP_VERIFY_TOKEN", "")).strip()
        mode = request.args.get("hub.mode", "")
        token = request.args.get("hub.verify_token", "")
        challenge = request.args.get("hub.challenge", "")
        if not verify_token:
            return Response("VERIFY_TOKEN is not configured.", status=503)
        if mode == "subscribe" and hmac.compare_digest(token, verify_token):
            return Response(challenge, status=200, mimetype="text/plain")
        return Response("Forbidden", status=403)

    @app.post("/webhook")
    def whatsapp_webhook_receive():
        raw_body = request.get_data(cache=True)
        app_secret = os.getenv("WHATSAPP_APP_SECRET", "").strip()
        if app_secret:
            signature = request.headers.get("X-Hub-Signature-256", "")
            if not _verify_signature(raw_body, signature, app_secret):
                return Response("Invalid signature", status=403)

        payload = request.get_json(silent=True) or {}
        if payload.get("object") != "whatsapp_business_account":
            return Response("", status=200)

        if _env_bool("WHATSAPP_ASYNC", True):
            threading.Thread(target=bot.handle_payload, args=(payload,), daemon=True).start()
        else:
            bot.handle_payload(payload)
        return Response("", status=200)

    return bot
