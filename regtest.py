"""Regression runner for the production CHECK path.

Discovers exam files, resets references through the existing admin operations,
then compares each CHECK result with the filename. It does not format exams.
"""

from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import job
import records
import reporting
from runtime_logging import event, error_fields
from service.config import Settings
from service.core import Service

STATUSES = ("ALL GOOD", "FORMATTING", "STRUCTURAL")
LABELS = (
    ("structural error", "STRUCTURAL"),
    ("structure error", "STRUCTURAL"),
    ("format error", "FORMATTING"),
    ("ok", "ALL GOOD"),
)
log_name = __import__("logging").getLogger("exam.regtest")


@dataclass(frozen=True)
class ExamCase:
    subject: str
    grade: str
    folder_name: str
    exam: Path
    reference: Path
    expected: str


@dataclass(frozen=True)
class SubjectInput:
    folder_name: str
    subject: str
    grade: str
    reference: Path
    exams: tuple[ExamCase, ...]


@dataclass
class CheckOutcome:
    status: str
    reason: str = ""
    seconds: float = 0.0
    cost: float | None = None


@dataclass
class InitRecord:
    folder_name: str
    reference_name: str
    loaded: str = "NOT RUN"
    activated: str = "NOT RUN"
    verified: str = "NOT RUN"
    detail: str = ""


@dataclass
class InitOutcome:
    ok: bool
    deleted: int = 0
    records: list[InitRecord] = field(default_factory=list)
    detail: str = ""


class RunLog:
    def __init__(self, folder: Path):
        self.folder = folder
        self.path = folder / "regtest_results.txt"
        self.handle = self.path.open("w", encoding="utf-8")

    def emit(self, text: str = "", console: bool = True) -> None:
        if console:
            print(text, flush=True)
        self.handle.write(text + "\n")
        self.handle.flush()

    def close(self) -> None:
        if not self.handle.closed:
            self.handle.close()


def filename_labels(name: str) -> list[tuple[str, str]]:
    """Return expected-result labels bounded by spaces, underscores, or hyphens."""
    stem = Path(name).stem.casefold()
    text = " ".join(stem.replace("_", " ").replace("-", " ").split())
    padded = f" {text} "
    found = []
    for phrase, status in LABELS:
        needle = f" {phrase} "
        if needle in padded:
            found.append((phrase, status))
            padded = padded.replace(needle, " ")
    return found


def expected_status(name: str) -> str:
    found = filename_labels(name)
    if not found:
        raise ValueError(f"{name} has no expected-result label.")
    if len(found) != 1:
        raise ValueError(f"{name} has conflicting expected-result labels.")
    return found[0][1]


def judge(expected: str, actual: str) -> str:
    if actual == expected and actual in STATUSES:
        return "PASS"
    return "FAIL"


def authenticate(typed: str, expected: str) -> bool:
    if not typed or not expected:
        return False
    left = typed.encode("utf-8")
    right = expected.encode("utf-8")
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def environment_problem(app_env: str, data_path: Path, project_data: Path) -> str:
    if str(app_env).strip().casefold() == "prod":
        return "APP_ENV is prod. Regression will not change a production environment."
    if Path(data_path).resolve() != Path(project_data).resolve():
        return "Reference data is not the local project data folder. Regression will not change it."
    return ""


def parse_subject_folder(name: str) -> tuple[str, str] | None:
    parts = name.strip().split()
    if len(parts) < 2:
        return None
    subject = " ".join(parts[:-1]).strip()
    grade = parts[-1].strip()
    if not subject or not grade or len(subject) > 80 or len(grade) > 20:
        return None
    if any(ord(ch) < 32 for ch in subject + grade):
        return None
    return subject, grade


def docx_files(folder: Path) -> list[Path]:
    found = []
    for path in folder.iterdir():
        if path.is_file() and path.suffix.casefold() == ".docx" and not path.name.startswith("~$"):
            found.append(path)
    return sorted(found, key=lambda item: item.name.casefold())


def discover(root: Path, skipped: list[str] | None = None) -> tuple[list[SubjectInput], list[str]]:
    if not root.is_dir():
        return [], [f"Missing input folder: {root}"]
    folders = sorted(
        (path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")),
        key=lambda path: path.name.casefold(),
    )
    if not folders:
        return [], ["No subject folders were found."]
    errors: list[str] = []
    subjects: list[SubjectInput] = []
    seen: dict[tuple[str, str], str] = {}
    for folder in folders:
        if folder.name[:1].casefold() == "x":
            if skipped is not None:
                skipped.append(folder.name)
            continue
        parsed = parse_subject_folder(folder.name)
        if parsed is None:
            errors.append(f"{folder.name} is not a valid subject and grade folder.")
            continue
        subject, grade = parsed
        key = (subject.casefold(), grade.casefold())
        if key in seen:
            errors.append(f"{folder.name} repeats {seen[key]}.")
            continue
        seen[key] = folder.name
        reference_dir = folder / "Reference"
        exam_dir = folder / "Exams"
        if not reference_dir.is_dir():
            errors.append(f"{folder.name} is missing its Reference folder.")
            continue
        if not exam_dir.is_dir():
            errors.append(f"{folder.name} is missing its Exams folder.")
            continue
        references = docx_files(reference_dir)
        if not references:
            errors.append(f"{folder.name} has no reference DOCX.")
            continue
        if len(references) > 1:
            errors.append(f"{folder.name} has more than one reference DOCX.")
            continue
        reference = references[0]
        if not _readable_docx(reference):
            errors.append(f"{reference.name} is not a readable Word document.")
            continue
        exams: list[ExamCase] = []
        blocked = False
        for exam in docx_files(exam_dir):
            try:
                expected = expected_status(exam.name)
            except ValueError as exc:
                errors.append(str(exc))
                blocked = True
                continue
            if not _readable_docx(exam):
                errors.append(f"{exam.name} is not a readable Word document.")
                blocked = True
                continue
            exams.append(ExamCase(subject, grade, folder.name, exam, reference, expected))
        if not blocked:
            subjects.append(SubjectInput(folder.name, subject, grade, reference, tuple(exams)))
    if not errors and not any(subject.exams for subject in subjects):
        errors.append("No exam DOCX files were found.")
    return subjects, errors


def allocate_run_dir(results: Path, day: str) -> Path:
    results.mkdir(parents=True, exist_ok=True)
    for number in range(1, 1000):
        folder = results / f"{day}_{number:03d}"
        if not folder.exists():
            folder.mkdir()
            return folder
    raise RuntimeError("Too many regression runs were recorded for this date.")


def configured_model() -> str:
    return (
        os.environ.get("EXAM_REDO_MODEL", "").strip()
        or os.environ.get("OPENAI_MODEL", "").strip()
        or "gpt-6-astra"
    )


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(records.ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


class ProductionAdmin:
    """Existing web/desktop admin reference operations."""

    def __init__(self, service: Service):
        self.service = service

    def list_references(self) -> list[dict]:
        return self.service.references()

    def delete_reference(self, ref_id: str) -> None:
        self.service.delete_reference(ref_id)

    def add_reference(self, subject: str, grade: str, data: bytes) -> dict:
        return self.service.save_reference(subject, grade, data, enabled=False)

    def activate_reference(self, ref_id: str) -> None:
        self.service.enable_reference(ref_id, True)

    def active_reference(self, subject: str, grade: str) -> dict | None:
        for ref in self.service.references():
            if ref["subject"].casefold() == subject.casefold() and str(ref["grade"]).casefold() == grade.casefold():
                return ref
        return None


def verify_reference(admin: ProductionAdmin, subject: str, grade: str, data: bytes) -> tuple[bool, str]:
    ref = admin.active_reference(subject, grade)
    if not ref or not ref.get("enabled"):
        return False, "Reference is not active."
    digest = hashlib.sha256(data).hexdigest()
    if ref.get("version") != digest:
        return False, "Registered reference does not match the input file."
    return True, ""


def initialize(subjects: list[SubjectInput], admin, keep: set[tuple[str, str]] | None = None) -> InitOutcome:
    keep = keep or set()
    records_out = [
        InitRecord(subject.folder_name, subject.reference.name)
        for subject in subjects
    ]
    deleted = 0
    try:
        current = list(admin.list_references())
        for ref in current:
            if _ref_key(ref) in keep:
                continue
            admin.delete_reference(ref["id"])
            deleted += 1
        leftover = [ref for ref in admin.list_references() if _ref_key(ref) not in keep]
        if leftover:
            return InitOutcome(False, deleted, records_out, "Reference registry is not empty after delete.")
    except Exception as exc:
        _log_failure("delete", exc)
        return InitOutcome(False, deleted, records_out, "Reference deletion failed.")
    for index, subject in enumerate(subjects):
        item = records_out[index]
        try:
            data = subject.reference.read_bytes()
            added = admin.add_reference(subject.subject, subject.grade, data)
            item.loaded = "PASS"
            admin.activate_reference(added["id"])
            item.activated = "PASS"
            ok, detail = verify_reference(admin, subject.subject, subject.grade, data)
            item.verified = "PASS" if ok else "FAIL"
            item.detail = detail
            if not ok:
                return InitOutcome(False, deleted, records_out, detail or "Reference verification failed.")
        except Exception as exc:
            _log_failure("load", exc)
            if item.loaded != "PASS":
                item.loaded = "FAIL"
            elif item.activated != "PASS":
                item.activated = "FAIL"
            else:
                item.verified = "FAIL"
            item.detail = "Reference loading failed."
            return InitOutcome(False, deleted, records_out, "Reference loading failed.")
    return InitOutcome(True, deleted, records_out)


def production_check(subject: str, grade: str, reference: Path, exam: Path) -> CheckOutcome:
    started = time.monotonic()
    before = len(_usage_rows())
    try:
        with tempfile.TemporaryDirectory(prefix="regtest-") as folder:
            result = job.run(
                "check",
                subject,
                grade,
                reference,
                exam,
                lambda message: print(message, flush=True),
                output_dir=Path(folder),
            )
    except Exception as exc:
        _log_failure("check", exc)
        return CheckOutcome("SYSTEM ERROR", "The check could not be completed.", time.monotonic() - started, _cost_since(before))
    status, reason = teacher_status(result)
    return CheckOutcome(status, reason, time.monotonic() - started, _cost_since(before))


def teacher_status(result) -> tuple[str, str]:
    if result is None or getattr(result, "uncertain", False) or getattr(result, "status", "") in {"", "SYSTEM ERROR"}:
        detail = "" if result is None else getattr(result, "detail", "")
        return "SYSTEM ERROR", _short(detail) or "The check did not finish."
    report = reporting.assess_engine(result.status, result.detail or "")
    if report is None or report.status not in STATUSES:
        return "SYSTEM ERROR", _short(result.detail) or "The check did not return a status."
    return report.status, _short(result.detail)


def run_session(
    root: Path,
    results: Path,
    *,
    admin_factory,
    checker,
    expected_password: str,
    app_env: str,
    data_path: Path,
    project_data: Path,
    confirm,
    model: str,
    typed_password: str | None = None,
    password_reader=None,
    today: str | None = None,
) -> int:
    day = today or datetime.now().strftime("%Y-%m-%d")
    folder = allocate_run_dir(results, day)
    record = RunLog(folder)
    started = time.monotonic()
    try:
        return _session(
            record, folder, root, started,
            admin_factory=admin_factory, checker=checker,
            expected_password=expected_password, app_env=app_env,
            data_path=data_path, project_data=project_data, confirm=confirm,
            model=model, typed_password=typed_password, password_reader=password_reader,
            day=day,
        )
    except KeyboardInterrupt:
        record.emit("")
        record.emit("Run interrupted.")
        record.emit("Original references were not restored.")
        record.emit(f"Report:\n{display_path(folder)}/")
        return 2
    finally:
        record.close()


def main() -> int:
    records.load_env()
    try:
        settings = Settings.load()
    except Exception as exc:
        _log_failure("configuration", exc)
        print("The local configuration could not be read.")
        return 1
    return run_session(
        records.ROOT / "Regression Test Inputs",
        records.ROOT / "Regression Test Results",
        admin_factory=lambda: ProductionAdmin(Service(settings)),
        checker=production_check,
        expected_password=os.environ.get("ADMIN_PASSWORD", ""),
        app_env=settings.app_env,
        data_path=settings.data,
        project_data=records.ROOT / "data",
        confirm=_confirm,
        model=configured_model(),
        password_reader=getpass.getpass,
    )


def _session(
    record: RunLog, folder: Path, root: Path, started: float, *,
    admin_factory, checker, expected_password: str, app_env: str, data_path: Path,
    project_data: Path, confirm, model: str, typed_password: str | None,
    password_reader, day: str,
) -> int:
    skipped: list[str] = []
    subjects, errors = discover(root, skipped)
    exam_count = sum(len(subject.exams) for subject in subjects)
    record.emit("EXAM FORMAT REV2 - REGRESSION TEST")
    record.emit("")
    record.emit(f"Run Date: {day}")
    record.emit(f"Model: {model}")
    record.emit("")
    record.emit("EXAM FORMAT REV2 REGRESSION TEST")
    record.emit("")
    record.emit(f"Found {len(subjects)} subjects.")
    record.emit(f"Found {exam_count} exam test cases.")
    for name in skipped:
        record.emit(f"SKIPPED: {name}")
    record.emit("")
    if errors:
        _initialization_header(record, 0, [])
        record.emit("INITIALIZATION RESULT: FAIL")
        record.emit("Input validation failed. References were not changed.")
        for error in errors:
            record.emit(f"- {error}")
        _finish(record, folder, started, None, 0, 0, "FAIL")
        return 1
    problem = environment_problem(app_env, data_path, project_data)
    if problem:
        _initialization_header(record, 0, [])
        record.emit("INITIALIZATION RESULT: FAIL")
        record.emit(problem)
        record.emit("References were not changed.")
        _finish(record, folder, started, None, 0, 0, "FAIL")
        return 1
    if not expected_password:
        record.emit("ADMIN_PASSWORD is not set. Add it to the environment before running.")
        _initialization_header(record, 0, [])
        record.emit("INITIALIZATION RESULT: FAIL")
        record.emit("ADMIN authentication failed.")
        _finish(record, folder, started, None, 0, 0, "FAIL")
        return 1
    typed = typed_password if typed_password is not None else _read_password(password_reader)
    if not authenticate(typed or "", expected_password):
        record.emit("ADMIN authentication failed.")
        _initialization_header(record, 0, [])
        record.emit("INITIALIZATION RESULT: FAIL")
        _finish(record, folder, started, None, 0, 0, "FAIL")
        return 1
    record.emit("WARNING:")
    record.emit("This will DELETE existing reference registrations")
    record.emit("and replace them with regression references.")
    record.emit("")
    if not confirm():
        record.emit("Run cancelled. References were not changed.")
        _finish(record, folder, started, None, 0, 0, "CANCELLED")
        return 0
    record.emit("Initializing references...")
    record.emit("")
    try:
        admin = admin_factory()
        outcome = initialize(subjects, admin, _keep_keys(skipped))
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        _log_failure("initialize", exc)
        outcome = InitOutcome(False, 0, [], "Reference loading failed.")
    _write_initialization(record, outcome)
    if not outcome.ok:
        record.emit("INITIALIZATION FAILED")
        record.emit(outcome.detail)
        record.emit("Original references were not restored.")
        record.emit("Exam tests were not started.")
        _finish(record, folder, started, None, 0, 0, "FAIL")
        return 1
    for item in outcome.records:
        record.emit(f"{item.folder_name} {'.' * max(1, 24 - len(item.folder_name))} PASS")
    record.emit("")
    record.emit("KNOWN STATE ESTABLISHED")
    record.emit("")
    record.emit("Running regression tests...")
    record.emit("")
    record.emit("================================", console=False)
    record.emit("REGRESSION TEST RESULTS", console=False)
    record.emit("================================", console=False)
    record.emit("", console=False)
    cases = [exam for subject in subjects for exam in subject.exams]
    passed = 0
    failed = 0
    costs: list[float | None] = []
    try:
        for index, exam in enumerate(cases, start=1):
            try:
                outcome_check = checker(exam.subject, exam.grade, exam.reference, exam.exam)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                _log_failure("check", exc)
                outcome_check = CheckOutcome("SYSTEM ERROR", "The check could not be completed.")
            result = judge(exam.expected, outcome_check.status)
            if result == "PASS":
                passed += 1
            else:
                failed += 1
            costs.append(outcome_check.cost)
            _write_test(record, index, len(cases), exam, outcome_check, result)
    except KeyboardInterrupt:
        record.emit("")
        record.emit("Run interrupted.")
        record.emit("Remaining tests were not run and are not passes.")
        record.emit("Original references were not restored.")
        _finish(record, folder, started, costs, passed, failed, "PASS")
        return 2
    record.emit("REGRESSION COMPLETE")
    record.emit("")
    _finish(record, folder, started, costs, passed, failed, "PASS")
    return 0 if failed == 0 else 1


def _ref_key(ref: dict) -> tuple[str, str]:
    return str(ref.get("subject", "")).casefold(), str(ref.get("grade", "")).casefold()


def _keep_keys(names: list[str]) -> set[tuple[str, str]]:
    keys = set()
    for name in names:
        parsed = parse_subject_folder(name)
        if parsed:
            keys.add((parsed[0].casefold(), parsed[1].casefold()))
    return keys


def _confirm() -> bool:
    answer = input("Continue? (Y/N): ").strip().casefold()
    return answer in {"y", "yes"}


def _read_password(reader) -> str:
    if reader is None or not sys.stdin.isatty():
        print("Admin password cannot be requested in this window.", flush=True)
        return ""
    return reader("Admin password: ")


def _initialization_header(record: RunLog, deleted: int, items: list[InitRecord]) -> None:
    record.emit("================================", console=False)
    record.emit("INITIALIZATION - KNOWN STATE", console=False)
    record.emit("================================", console=False)
    record.emit("", console=False)
    record.emit(f"Existing references deleted: {deleted}", console=False)
    record.emit("", console=False)
    for item in items:
        record.emit(item.folder_name, console=False)
        record.emit(f"  Reference: {item.reference_name}", console=False)
        record.emit(f"  Loaded: {item.loaded}", console=False)
        record.emit(f"  Activated: {item.activated}", console=False)
        record.emit(f"  Verified: {item.verified}", console=False)
        if item.detail:
            record.emit(f"  Detail: {item.detail}", console=False)
        record.emit("", console=False)


def _write_initialization(record: RunLog, outcome: InitOutcome) -> None:
    _initialization_header(record, outcome.deleted, outcome.records)
    record.emit(f"INITIALIZATION RESULT: {'PASS' if outcome.ok else 'FAIL'}", console=False)
    if outcome.detail and not outcome.ok:
        record.emit(outcome.detail, console=False)
        record.emit("Original references were not restored.", console=False)
    record.emit("", console=False)


def _write_test(record: RunLog, index: int, total: int, exam: ExamCase, outcome: CheckOutcome, result: str) -> None:
    label = {"ALL GOOD": "OK", "FORMATTING": "FORMAT", "STRUCTURAL": "STRUCTURE"}.get(exam.expected, exam.expected)
    line = f"[{index}/{total}] {exam.folder_name} - {label} "
    record.emit(f"{line}{'.' * max(1, 42 - len(line))} {result}")
    record.emit("")
    record.emit(f"TEST {index:03d}", console=False)
    record.emit(f"Subject: {exam.folder_name}", console=False)
    record.emit(f"Exam: {exam.exam.name}", console=False)
    record.emit(f"Reference: {exam.reference.name}", console=False)
    record.emit(f"Expected: {exam.expected}", console=False)
    record.emit(f"Actual: {outcome.status or 'MISSING'}", console=False)
    record.emit(f"Result: {result}", console=False)
    if result != "PASS":
        reason = outcome.reason or f"Expected {exam.expected} but the check returned {outcome.status or 'MISSING'}."
        record.emit(f"Reason: {reason}", console=False)
    record.emit(f"Time: {outcome.seconds:.1f} seconds", console=False)
    record.emit(f"Estimated API cost: {_money(outcome.cost)}", console=False)
    record.emit("", console=False)


def _finish(record: RunLog, folder: Path, started: float, costs: list[float | None] | None, passed: int, failed: int, initialization: str) -> None:
    total = passed + failed
    record.emit("================================")
    record.emit("REGRESSION TEST SUMMARY")
    record.emit("================================")
    record.emit("")
    record.emit(f"Total Tests: {total}")
    record.emit(f"PASSED: {passed}")
    record.emit(f"FAILED: {failed}")
    record.emit("")
    record.emit(f"Initialization: {initialization}")
    record.emit(f"Total Time: {time.monotonic() - started:.1f} seconds")
    record.emit(f"Estimated API Cost: {_total_cost(costs)}")
    record.emit("")
    record.emit("================================")
    record.emit("")
    record.emit("Report:")
    record.emit(f"{display_path(folder)}/")


def _total_cost(costs: list[float | None] | None) -> str:
    if not costs or any(cost is None for cost in costs):
        return "not available"
    return _money(sum(costs))


def _money(amount: float | None) -> str:
    if amount is None:
        return "not available"
    return f"${amount:.4f}"


def _readable_docx(path: Path) -> bool:
    try:
        return records.is_word_package(path.read_bytes())
    except OSError:
        return False


def _usage_rows() -> list[dict]:
    if not records.USAGE.is_file():
        return []
    rows = []
    for raw in records.USAGE.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _cost_since(before: int) -> float | None:
    rows = _usage_rows()[before:]
    amounts = []
    for row in rows:
        if row.get("usd") is None:
            continue
        try:
            amounts.append(float(row["usd"]))
        except (TypeError, ValueError):
            continue
    if not amounts:
        return None
    return sum(amounts)


def _short(text: str) -> str:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return ""
    return job.redact(cleaned)[:240]


def _log_failure(stage: str, exc: BaseException) -> None:
    event(log_name, "regtest.failed", stage=stage, **error_fields(exc))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(2)
