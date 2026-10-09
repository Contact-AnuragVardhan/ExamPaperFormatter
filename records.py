"""Reference files, the activity log, and API cost totals."""

from __future__ import annotations

import json
import os
import threading
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
REFERENCES = DATA / "references"
INDEX = REFERENCES / "index.json"
LOG = DATA / "activity.log"
USAGE = DATA / "usage.jsonl"

_LOCK = threading.Lock()

# Published standard rates, dollars per 1M tokens, plus the container session rate.
# Used only to turn token counts from the API response into a dollar figure.
_TOKEN_RATES = {
    "gpt-6-astra": {
        "input": 10.0,
        "cached": 1.0,
        "cache_write": 12.5,
        "output": 50.0,
        "long_context_at": 272_000,
    }
}
_CONTAINER_PER_20_MIN = {"1g": 0.03, "4g": 0.12, "16g": 0.48, "64g": 1.92}


@dataclass(frozen=True)
class Reference:
    subject: str
    grade: str
    path: Path


def load_env() -> None:
    path = ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def is_word_package(data: bytes) -> bool:
    """True when the bytes are a Word package. Does not read document text."""
    if not data.startswith(b"PK"):
        return False
    try:
        with zipfile.ZipFile(BytesIO(data)) as package:
            names = set(package.namelist())
    except zipfile.BadZipFile:
        return False
    return "[Content_Types].xml" in names and "word/document.xml" in names


def list_references() -> list[Reference]:
    with _LOCK:
        rows = _read_index()
    refs = [
        Reference(row["subject"], row["grade"], REFERENCES / row["file"])
        for row in rows
    ]
    refs.sort(key=lambda ref: (ref.subject.casefold(), ref.grade.casefold()))
    return refs


def find_reference(subject: str, grade: str) -> Reference | None:
    key = (subject.strip().casefold(), grade.strip().casefold())
    for ref in list_references():
        if (ref.subject.casefold(), ref.grade.casefold()) == key:
            return ref
    return None


def save_reference(subject: str, grade: str, data: bytes) -> tuple[str, Reference]:
    subject = subject.strip()
    grade = grade.strip()
    with _LOCK:
        rows = _read_index()
        existing = _match(rows, subject, grade)
        REFERENCES.mkdir(parents=True, exist_ok=True)
        if existing is None:
            filename = f"{uuid.uuid4().hex}.docx"
            (REFERENCES / filename).write_bytes(data)
            rows.append({"subject": subject, "grade": grade, "file": filename})
            _write_index(rows)
            action = "ADDED"
        else:
            filename = existing["file"]
            (REFERENCES / filename).write_bytes(data)
            subject = existing["subject"]
            grade = existing["grade"]
            action = "REPLACED"
    ref = Reference(subject, grade, REFERENCES / filename)
    log(subject, grade, "REF", action)
    return action, ref


def delete_reference(subject: str, grade: str) -> Reference | None:
    with _LOCK:
        rows = _read_index()
        existing = _match(rows, subject, grade)
        if existing is None:
            return None
        kept = [row for row in rows if row is not existing]
        _write_index(kept)
        path = REFERENCES / existing["file"]
        path.unlink(missing_ok=True)
        ref = Reference(existing["subject"], existing["grade"], path)
    log(ref.subject, ref.grade, "REF", "DELETED")
    return ref


def log(subject: str, grade: str, action: str, result: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"{stamp} | {_cell(subject)} | {_cell(grade)} | {_cell(action)} | {_cell(result)}\n"
    with _LOCK:
        DATA.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(line)


def read_log() -> str:
    if not LOG.is_file():
        return ""
    lines = LOG.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[-300:])


def save_result(subject: str, grade: str, role: str, data: bytes) -> Path:
    folder = DATA / "results"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{stamp}_{_filename_piece(subject)}_{_filename_piece(grade)}_{role}_{uuid.uuid4().hex[:8]}.docx"
    path = folder / name
    path.write_bytes(data)
    return path


def record_usage(entry: dict) -> None:
    with _LOCK:
        DATA.mkdir(parents=True, exist_ok=True)
        with USAGE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")


def cost_totals() -> tuple[float, float, float, bool]:
    now = datetime.now()
    today = 0.0
    month = 0.0
    all_time = 0.0
    unpriced = False
    if not USAGE.is_file():
        return today, month, all_time, unpriced
    for raw in USAGE.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if row.get("tokens_unpriced"):
            unpriced = True
        if row.get("usd") is None:
            continue
        amount = float(row["usd"])
        all_time += amount
        try:
            at = datetime.fromisoformat(row["at"])
        except (TypeError, ValueError):
            continue
        if at.date() == now.date():
            today += amount
        if at.year == now.year and at.month == now.month:
            month += amount
    return today, month, all_time, unpriced


def price_call(usage: dict | None, elapsed_seconds: float, model: str, memory: str = "4g") -> dict:
    rates = _TOKEN_RATES.get(model)
    input_tokens = 0
    cached_tokens = 0
    cache_write_tokens = 0
    output_tokens = 0
    if usage:
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        details = usage.get("input_tokens_details") or {}
        cached_tokens = max(0, min(int(details.get("cached_tokens") or 0), input_tokens))
        cache_write_tokens = max(
            0,
            min(int(details.get("cache_write_tokens") or 0), input_tokens - cached_tokens),
        )
    plain_tokens = input_tokens - cached_tokens - cache_write_tokens
    token_usd = 0.0
    unpriced = rates is None
    if rates is not None:
        long_context = input_tokens > rates["long_context_at"]
        input_scale = 2.0 if long_context else 1.0
        output_scale = 1.5 if long_context else 1.0
        million = 1_000_000
        token_usd = (
            plain_tokens * rates["input"] * input_scale
            + cached_tokens * rates["cached"] * input_scale
            + cache_write_tokens * rates["cache_write"] * input_scale
            + output_tokens * rates["output"] * output_scale
        ) / million
    session = _CONTAINER_PER_20_MIN.get(memory, _CONTAINER_PER_20_MIN["4g"])
    minutes = max(5, int((elapsed_seconds + 59) // 60))
    container_usd = minutes * (session / 20.0)
    return {
        "at": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "cache_write_tokens": cache_write_tokens,
        "output_tokens": output_tokens,
        "container_usd": round(container_usd, 6),
        "tokens_unpriced": unpriced,
        "usd": round(token_usd + container_usd, 6),
    }


def _read_index() -> list[dict]:
    if not INDEX.is_file():
        return []
    data = json.loads(INDEX.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise RuntimeError("The reference list could not be read.")
    return data


def _write_index(rows: list[dict]) -> None:
    REFERENCES.mkdir(parents=True, exist_ok=True)
    temporary = INDEX.with_suffix(".tmp")
    temporary.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    temporary.replace(INDEX)


def _match(rows: list[dict], subject: str, grade: str) -> dict | None:
    key = (subject.strip().casefold(), grade.strip().casefold())
    for row in rows:
        if (str(row["subject"]).strip().casefold(), str(row["grade"]).strip().casefold()) == key:
            return row
    return None


def _cell(value: str) -> str:
    return " ".join(str(value).split()).replace("|", "/")


def _filename_piece(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in value.strip()).strip("_")
    return cleaned[:40] or "file"
