"""Teacher-facing exam check statuses shared by Windows and WhatsApp.

The formatter still returns its own result. This module only decides what the
teacher sees, and whether that result may proceed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import records
from runtime_logging import event

ALL_GOOD = "ALL GOOD"
FORMATTING = "FORMATTING"
STRUCTURAL = "STRUCTURAL"

_READY = "Your exam is ready. You can proceed."
_FORMAT = "Some formatting needs attention. You can proceed."
_OPTIONAL = frozenset({"header", "section", "image"})
_UNCERTAIN = (
    "unclear", "uncertain", "not sure", "unsure", "cannot tell", "can't tell",
    "might", "may be", "possibly", "appears to", "could be",
)
_FORMAT_CUES = (
    "font", "spacing", "indent", "alignment", "align", "margin", "bold",
    "underline", "numbering", "layout", "presentation",
)
_STRUCTURAL_CUES = (
    "missing", "absent", "not included", "not present", "cannot be completed",
    "can't be completed", "invent", "answer key", "required content",
)
_REQUIRES = (
    "reference requires", "reference expects", "required by the reference",
    "the reference has", "the reference includes", "the reference contains",
    "reference includes", "reference contains",
)


@dataclass(frozen=True)
class Finding:
    message: str
    category: str
    confirmed: bool = True
    element: str = ""
    required_by_reference: bool = False


@dataclass(frozen=True)
class Report:
    status: str
    message: str
    proceed: bool
    structural: tuple[str, ...]
    formatting: tuple[str, ...]
    withheld: tuple[str, ...]


def requirements_for(reference_id, by_reference) -> frozenset[str]:
    """Requirements belong to the selected reference only."""
    if not reference_id or not by_reference:
        return frozenset()
    return frozenset(str(item).casefold() for item in by_reference.get(reference_id, ()))


def assess(findings, reference_requires=()) -> Report:
    required = {str(item).casefold() for item in reference_requires}
    structural: list[str] = []
    formatting: list[str] = []
    withheld: list[str] = []
    for finding in findings:
        text = _plain(finding.message)
        if not text:
            continue
        if _is_structural(finding, required):
            _add(structural, text)
        elif finding.category == "formatting" and finding.confirmed:
            _add(formatting, text)
        else:
            _add(withheld, text)
    if structural:
        return Report(STRUCTURAL, _structural_message(structural), False,
                      tuple(structural), tuple(formatting), tuple(withheld))
    if formatting:
        return Report(FORMATTING, _FORMAT, True, (), tuple(formatting), tuple(withheld))
    return Report(ALL_GOOD, _READY, True, (), (), tuple(withheld))


def assess_engine(status: str, detail: str = "", reference_requires=()) -> Report | None:
    """Classify an existing formatter status without changing the formatter."""
    if status in {"OK", "PASS"}:
        return assess(())
    if status == "FORMAT ERROR":
        lines = _lines(detail) or ["Formatting differs from the reference."]
        return assess(tuple(Finding(line, "formatting") for line in lines), reference_requires)
    if status == "STRUCTURAL ERROR":
        lines = _lines(detail)
        if not lines:
            lines = ["A required part of this exam is missing and cannot be completed without adding academic content."]
            return assess((Finding(lines[0], "structural", confirmed=True),), reference_requires)
        return assess(tuple(_line_finding(line, reference_requires) for line in lines), reference_requires)
    return None


def remember(report: Report, *, engine_status: str, engine_detail: str = "", job_id: str = "", logger=None) -> None:
    """Keep every finding, including issues hidden from the teacher."""
    logger = logger or logging.getLogger("exam.report")
    event(logger, "exam.validation.summary", job_id=job_id, engine_status=engine_status,
          report_status=report.status, proceed=report.proceed,
          structural_count=len(report.structural), formatting_count=len(report.formatting),
          withheld_count=len(report.withheld))
    _log_text(logger, "exam.validation.engine_detail", engine_detail, job_id=job_id, engine_status=engine_status)
    for category, items, shown in (
        ("structural", report.structural, report.status == STRUCTURAL),
        ("formatting", report.formatting, False),
        ("withheld", report.withheld, False),
    ):
        for index, text in enumerate(items):
            _log_text(logger, "exam.validation.issue", text, job_id=job_id,
                      category=category, index=index, shown=shown)
    if report.structural or report.formatting or report.withheld or str(engine_detail).strip():
        records.log("validation", report.status, engine_status, "proceed" if report.proceed else "block")
        for text in report.structural:
            records.log("validation", "structural", "shown", text)
        for text in report.formatting:
            records.log("validation", "formatting", "logged", text)
        for text in report.withheld:
            records.log("validation", "withheld", "logged", text)
        if str(engine_detail).strip():
            records.log("validation", "engine_detail", engine_status, engine_detail)


def _is_structural(finding: Finding, required: set[str]) -> bool:
    if finding.category != "structural" or not finding.confirmed:
        return False
    element = finding.element.casefold()
    if element in _OPTIONAL:
        return bool(finding.required_by_reference or element in required)
    return True


def _line_finding(line: str, reference_requires) -> Finding:
    required = {str(item).casefold() for item in reference_requires}
    element = _element(line)
    justified = _contains(line, _REQUIRES) or bool(element and element in required)
    if _contains(line, _UNCERTAIN):
        return Finding(line, "structural", confirmed=False, element=element, required_by_reference=justified)
    if element in _OPTIONAL and not justified:
        return Finding(line, "formatting", element=element)
    if _contains(line, _FORMAT_CUES) and not _contains(line, _STRUCTURAL_CUES):
        return Finding(line, "formatting", element=element)
    return Finding(line, "structural", confirmed=True, element=element, required_by_reference=justified)


def _structural_message(issues: list[str]) -> str:
    bullets = "\n".join(f"- {issue}" for issue in issues)
    return f"Please fix these before continuing:\n{bullets}\n\nUpload the corrected exam."


def _plain(text: str) -> str:
    cleaned = " ".join(str(text).split())
    for prefix in ("Problem:", "Issue:", "STRUCTURAL:", "FORMAT ERROR:", "FORMATTING:", "FORMAT:"):
        if cleaned.casefold().startswith(prefix.casefold()):
            cleaned = cleaned[len(prefix):].strip()
    return cleaned


def _lines(detail: str) -> list[str]:
    rows = []
    for raw in str(detail or "").replace("\r\n", "\n").split("\n"):
        line = _plain(raw.lstrip("-•* "))
        if line:
            rows.append(line)
    return rows


def _element(line: str) -> str:
    text = line.casefold()
    if any(word in text for word in ("image", "figure", "picture", "diagram", "photo")):
        return "image"
    if "header" in text or "heading" in text:
        return "header"
    if "section" in text:
        return "section"
    return ""


def _contains(line: str, cues: tuple[str, ...]) -> bool:
    text = line.casefold()
    return any(cue in text for cue in cues)


def _add(items: list[str], text: str) -> None:
    if text not in items:
        items.append(text)


def _log_text(logger, name: str, text: str, **fields) -> None:
    body = str(text or "")
    if not body:
        return
    parts = [body[index:index + 200] for index in range(0, len(body), 200)]
    for index, part in enumerate(parts):
        event(logger, name, part_index=index, part_count=len(parts), text=part, **fields)
