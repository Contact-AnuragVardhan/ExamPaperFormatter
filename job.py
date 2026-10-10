"""Move two Word files to ChatGPT Code Interpreter and save the file it returns.

This module does not read exam content or decide formatting.
"""

from __future__ import annotations

import os
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import records
from runtime_logging import event,error_fields
from instructions import CHECK_INSTRUCTIONS, FORMAT_INSTRUCTIONS

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MEMORY = "4g"
DEADLINE_SECONDS = 25 * 60
CHECK_STATUS = {"OK", "FORMAT ERROR", "STRUCTURAL ERROR"}
FORMAT_STATUS = {"PASS", "STRUCTURAL ERROR"}
FILE_FOR_STATUS = {
    "PASS": "revised.docx",
    "FORMAT ERROR": "details.docx",
    "STRUCTURAL ERROR": "details.docx",
}
log = logging.getLogger('exam.engine')


@dataclass
class Result:
    status: str
    detail: str
    saved_path: Path | None = None
    retryable: bool = False
    uncertain: bool = False
    error_code: str = ""
    diagnostic: dict | None = None


def run(
    kind: str,
    subject: str,
    grade: str,
    reference_path: Path,
    teacher_path: Path,
    on_progress=None,
    *,
    output_dir: Path | None = None,
    remote_state: dict | None = None,
    on_remote=None,
) -> Result:
    on_progress = on_progress or (lambda _message: None)
    on_remote = on_remote or (lambda _state: None)
    remote_state = remote_state or {}
    if kind not in {"check", "format"}:
        return Result("SYSTEM ERROR", "Unknown operation.", error_code="configuration")
    records.load_env()
    try:
        from openai import OpenAI
    except ImportError:
        return Result(
            "SYSTEM ERROR",
            "The openai package is not installed. Run: py -3 -m pip install -r requirements.txt",
        )

    instructions = CHECK_INSTRUCTIONS if kind == "check" else FORMAT_INSTRUCTIONS
    allowed = CHECK_STATUS if kind == "check" else FORMAT_STATUS
    client = None
    container_id = None
    response_id = None
    usage = None
    started = None
    submitting = False
    uncertain = False
    stage = "configuration"
    model = (os.environ.get("EXAM_REDO_MODEL", "").strip()
             or os.environ.get("OPENAI_MODEL", "").strip() or "gpt-6-astra")
    event(log,'openai.job.started',kind=kind,subject=subject,grade=grade,model=model,
          resume_existing=bool(remote_state.get('response_id')))
    try:
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            event(log,'openai.configuration.missing',level=logging.ERROR,setting='OPENAI_API_KEY')
            return Result("SYSTEM ERROR", "OPENAI_API_KEY is not set.", error_code="configuration")
        effort = os.environ.get("OPENAI_REASONING_EFFORT", "").strip() or "high"
        if effort not in {"low", "medium", "high", "xhigh", "max"}:
            event(log,'openai.configuration.invalid',level=logging.ERROR,setting='OPENAI_REASONING_EFFORT')
            return Result(
                "SYSTEM ERROR",
                "OPENAI_REASONING_EFFORT must be low, medium, high, xhigh, or max.",
            )
        on_progress("Sending the Reference and the Teacher Exam to ChatGPT...")
        client = OpenAI(api_key=api_key, timeout=300.0, max_retries=0)
        started = time.monotonic()
        if remote_state.get("response_id") and remote_state.get("container_id"):
            container_id = remote_state["container_id"]
            response_id = remote_state["response_id"]
            stage = "retrieve_response"
            event(log,'openai.response.resuming',response_id=response_id,container_id=container_id)
            response = client.responses.retrieve(response_id)
        else:
            stage = "create_container"
            event(log,'openai.container.creating',memory=MEMORY)
            container = client.containers.create(name="exam-formatter-rev2", memory_limit=MEMORY)
            container_id = container.id
            event(log,'openai.container.created',container_id=container_id)
            on_remote({"container_id": container_id})
            stage = "upload_reference"
            event(log,'openai.file.uploading',role='reference',bytes=reference_path.stat().st_size)
            reference = _upload(client, container_id, "reference.docx", reference_path)
            stage = "upload_teacher"
            event(log,'openai.file.uploading',role='teacher',bytes=teacher_path.stat().st_size)
            teacher = _upload(client, container_id, "teacher.docx", teacher_path)
            # Persist intent before the paid POST. A crash in this narrow window is
            # marked uncertain instead of automatically repeating a paid request.
            submitted_at = time.time()
            on_remote({"container_id": container_id, "submitting": True, "submitted_at": submitted_at})
            submitting = True
            stage = "submit_response"
            event(log,'openai.response.submitting',model=model,reasoning_effort=effort,container_id=container_id)
            response = client.responses.create(
                model=model,
                instructions=instructions,
                input=_task(kind, reference.path, teacher.path),
                tools=[{"type": "code_interpreter", "container": container_id}],
                tool_choice="required",
                reasoning={"effort": effort},
                max_output_tokens=100000,
                background=True,
            )
            response_id = response.id
            event(log,'openai.response.submitted',response_id=response_id,status=response.status)
            stage = "persist_response"
            on_remote({"container_id": container_id, "response_id": response_id, "submitted_at": submitted_at})
        elapsed_before_resume = max(0, time.time() - remote_state.get("submitted_at", time.time()))
        deadline = started + max(0, DEADLINE_SECONDS - elapsed_before_resume)
        heartbeat_at=0
        last_status=None
        while response.status in {"queued", "in_progress"}:
            now=time.monotonic()
            event(log,'openai.response.poll',level=logging.DEBUG,response_id=response_id,status=response.status)
            if response.status!=last_status or now-heartbeat_at>=30:
                event(log,'openai.response.waiting',response_id=response_id,status=response.status,
                      elapsed_seconds=round(elapsed_before_resume+now-started),deadline_seconds=DEADLINE_SECONDS)
                heartbeat_at=now;last_status=response.status
            if time.monotonic() > deadline:
                confirmed = _cancel(client, response_id)
                uncertain = not confirmed
                event(log,'openai.response.timeout',level=logging.ERROR,response_id=response_id,cancellation_confirmed=confirmed)
                return Result(
                    "SYSTEM ERROR",
                    f"ChatGPT did not finish within 25 minutes. Response {response_id}.",
                    retryable=confirmed, uncertain=not confirmed, error_code="timeout",
                    diagnostic={"stage":"wait_response", "model":model, "response_id":response_id,
                                "message":"25-minute deadline exceeded; cancellation " + ("confirmed." if confirmed else "unconfirmed.")},
                )
            minutes = int((time.monotonic() - started) // 60)
            on_progress(f"ChatGPT is working on the Word files... {minutes} min")
            time.sleep(4)
            stage = "retrieve_response"
            response = client.responses.retrieve(response_id)
        stage = "inspect_response"
        event(log,'openai.response.finished',response_id=response_id,status=response.status,
              elapsed_seconds=round(time.monotonic()-started,2))
        usage = _usage_dict(response)
        if response.status != "completed":
            event(log,'openai.response.incomplete',level=logging.ERROR,response_id=response_id,status=response.status)
            return Result("SYSTEM ERROR", _response_error(response), retryable=True,
                          error_code="incomplete" if response.status == "incomplete" else "provider_failed")

        text = response.output_text or ""
        status, note = _status_line(text, allowed)
        if status is None:
            event(log,'openai.result.unrecognized',level=logging.ERROR,response_id=response_id)
            return Result(
                "SYSTEM ERROR",
                "ChatGPT did not return a recognized result.\n\n" + text.strip(),
                error_code="unrecognized",
            )
        if status == "OK":
            event(log,'openai.result.classified',result_status=status,response_id=response_id)
            on_remote({"container_id":container_id,"response_id":response_id,
                       "result_status":status,"output_file":None})
            return Result(status, note)

        on_progress("Saving the Word file...")
        filename = FILE_FOR_STATUS[status]
        stage = "download_output"
        event(log,'openai.output.downloading',response_id=response_id,result_status=status,expected_file=filename)
        data, present = _download(client, container_id, response, filename)
        if data is None:
            event(log,'openai.output.missing',level=logging.ERROR,expected_file=filename,file_count=len(present))
            listed = ", ".join(present) if present else "none"
            return Result(
                "SYSTEM ERROR",
                f"ChatGPT did not return {filename}. Files present: {listed}\n\n{text.strip()}",
                error_code="missing_output",
            )
        if not records.is_word_package(data):
            event(log,'openai.output.invalid',level=logging.ERROR,bytes=len(data))
            return Result(
                "SYSTEM ERROR",
                "ChatGPT returned a file that is not a Word document.",
                error_code="invalid_output",
            )
        role = "revised" if filename == "revised.docx" else "details"
        stage = "save_output"
        if output_dir is None:
            saved = records.save_result(subject, grade, role, data)
        else:
            output_dir.mkdir(parents=True, exist_ok=True)
            saved = output_dir / filename
            saved.write_bytes(data)
        on_remote({"container_id":container_id,"response_id":response_id,
                   "result_status":status,"output_file":saved.name})
        detail = note
        event(log,'openai.output.saved',result_status=status,bytes=len(data),response_id=response_id)
        return Result(status, detail.strip(), saved)
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        # A lost POST response may already have started a paid request. Never
        # classify that as a safe retry. Known IDs can be resumed by the admin.
        # These HTTP responses reject the request. A network failure or 5xx
        # during submission may have started it despite losing the reply.
        rejected = status_code in {400, 401, 403, 404, 405, 413, 415, 422, 429}
        uncertain = bool(response_id) or (submitting and not rejected)
        if submitting and rejected and not response_id:
            on_remote({"container_id":container_id, "submitting":False,
                       "response_rejected":True, "http_status":status_code})
        event(log,'openai.request.failed',level=logging.ERROR,stage=stage,model=model,
              response_id=response_id,uncertain=uncertain,**error_fields(exc))
        return Result("SYSTEM ERROR", _public_error(exc),
                      retryable=(status_code in {429, 503}) and not uncertain, uncertain=uncertain,
                      error_code="busy" if status_code == 429 else ("configuration" if rejected else "provider_failed"),
                      diagnostic={"stage":stage, "model":model, "error_type":type(exc).__name__,
                                  "http_status":status_code, "response_id":response_id,
                                  "request_id":getattr(exc,"request_id",None), "message":_public_error(exc)})
    finally:
        if started is not None:
            try:
                records.record_usage(
                    records.price_call(usage, time.monotonic() - started, model, MEMORY)
                )
                event(log,'openai.usage.recorded',response_id=response_id,elapsed_seconds=round(time.monotonic()-started,2),
                      input_tokens=(usage or {}).get('input_tokens'),output_tokens=(usage or {}).get('output_tokens'))
            except Exception as exc:
                event(log,'openai.usage.failed',level=logging.WARNING,**error_fields(exc))
        if client is not None and container_id and not uncertain:
            try:
                client.containers.delete(container_id)
                event(log,'openai.container.deleted',container_id=container_id)
            except Exception as exc:
                event(log,'openai.container.cleanup_failed',level=logging.WARNING,container_id=container_id,**error_fields(exc))
        elif container_id and uncertain:
            event(log,'openai.container.retained',container_id=container_id,response_id=response_id,reason='unresolved_request')


def _upload(client, container_id: str, name: str, path: Path):
    payload = path.read_bytes()
    return client.containers.files.create(
        container_id,
        file=(name, payload, DOCX_MIME),
    )


def _task(kind: str, reference_path: str, teacher_path: str) -> str:
    if kind == "check":
        ask = "Check the teacher exam against the reference. Do not modify the teacher file."
    else:
        ask = (
            "Format the teacher exam so its academic content is presented "
            "in the reference's form. Write the Word file in this container."
        )
    return (
        f"{ask}\n\n"
        f"Reference .docx path:\n{reference_path}\n\n"
        f"Teacher exam .docx path:\n{teacher_path}\n\n"
        "Open those paths with the python tool. "
        "The documents are not pasted into this message.\n"
        "Your reply must begin with the status line."
    )


def _download(client, container_id: str, response, filename: str) -> tuple[bytes | None, list[str]]:
    files = list(client.containers.files.list(container_id, limit=100))
    present = [Path(item.path).name for item in files]
    matches = [item for item in files if Path(item.path).name.lower() == filename.lower()]
    assistants = [item for item in matches if getattr(item, "source", None) == "assistant"]
    pool = assistants or matches
    file_id = None
    if pool:
        file_id = max(pool, key=lambda item: getattr(item, "created_at", 0)).id
    else:
        file_id = _citation_id(response, filename)
    if not file_id:
        return None, present
    downloaded = client.containers.files.content.retrieve(file_id, container_id=container_id)
    return downloaded.read(), present


def _citation_id(response, filename: str) -> str | None:
    try:
        payload = response.model_dump()
    except Exception:
        return None
    found: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            if node.get("type") == "container_file_citation":
                name = Path(str(node.get("filename") or "")).name
                if name.lower() == filename.lower() and node.get("file_id"):
                    found.append(str(node["file_id"]))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return found[-1] if found else None


def _status_line(text: str, allowed: set[str]) -> tuple[str | None, str]:
    lines = [line.strip() for line in text.replace("\r\n", "\n").split("\n")]
    lines = [line for line in lines if line]
    if not lines:
        return None, ""
    first = lines[0].strip("`*_").rstrip(".").strip()
    if first in allowed:
        return first, "\n".join(lines[1:]).strip()
    return None, text.strip()


def _usage_dict(response) -> dict | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    return None


def _response_error(response) -> str:
    err = getattr(response, "error", None)
    if err is None:
        return f"ChatGPT returned status {response.status}."
    message = getattr(err, "message", None)
    if message:
        return str(message)
    return str(err)


def _cancel(client, response_id: str) -> bool:
    event(log,'openai.response.cancel_requested',response_id=response_id)
    try:
        response = client.responses.cancel(response_id)
        event(log,'openai.response.cancel_result',response_id=response_id,status=response.status)
        return response.status == "cancelled"
    except Exception as exc:
        event(log,'openai.response.cancel_failed',level=logging.ERROR,response_id=response_id,**error_fields(exc))
        return False


def _public_error(exc: BaseException) -> str:
    return redact(str(exc).strip() or exc.__class__.__name__)


def redact(text: str, extra_secrets=()) -> str:
    """Sanitize bounded diagnostic text without exposing configured credentials."""
    secrets = [os.environ.get(name, "").strip() for name in (
        "OPENAI_API_KEY", "WHATSAPP_TOKEN", "WHATSAPP_ACCESS_TOKEN",
        "WHATSAPP_APP_SECRET", "VERIFY_TOKEN", "WHATSAPP_VERIFY_TOKEN",
        "ADMIN_PASSWORD", "WEB_TEACHER_ACCESS_CODE")]
    for secret in sorted(set(secrets) | set(extra_secrets), key=len, reverse=True):
        if secret:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r"sk-[A-Za-z0-9_\-]+", "[redacted]", text)
    text = re.sub(r"(?i)Bearer\s+[^\s'\"<>]+", "Bearer [redacted]", text)
    if len(text) > 4000:
        text = text[:4000] + "..."
    return text
