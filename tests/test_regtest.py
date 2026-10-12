"""Regression runner tests. Admin and CHECK are mocked; no API calls."""
import hashlib
from pathlib import Path

import job
import records
import regtest
from service.config import Settings
from service.core import Service

SECRET = "hidden-admin-secret"


class FakeAdmin:
    def __init__(self, existing=()):
        self.refs = [dict(ref) for ref in existing]
        self.deleted = []
        self.added = []
        self.activated = []
        self.fail = ""

    def list_references(self):
        return [dict(ref) for ref in self.refs]

    def delete_reference(self, ref_id):
        if self.fail == "delete":
            raise RuntimeError("delete failed")
        self.deleted.append(ref_id)
        self.refs = [ref for ref in self.refs if ref["id"] != ref_id]

    def add_reference(self, subject, grade, data):
        if self.fail == "add":
            raise RuntimeError("add failed")
        ref = {
            "id": f"id-{subject}-{grade}",
            "subject": subject,
            "grade": grade,
            "enabled": False,
            "version": "wrong" if self.fail == "hash" else hashlib.sha256(data).hexdigest(),
        }
        self.added.append((subject, grade, data))
        self.refs.append(ref)
        return ref

    def activate_reference(self, ref_id):
        if self.fail == "activate":
            raise RuntimeError("activate failed")
        self.activated.append(ref_id)
        for ref in self.refs:
            if ref["id"] == ref_id:
                ref["enabled"] = True

    def active_reference(self, subject, grade):
        for ref in self.refs:
            if ref["subject"].casefold() == subject.casefold() and str(ref["grade"]).casefold() == grade.casefold():
                return ref
        return None


class FakeCheck:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, subject, grade, reference, exam):
        self.calls.append((subject, grade, Path(reference).name, Path(exam).name))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def write_tree(root: Path, word: bytes, exams=("english10_OK.docx",)):
    folder = root / "English 10"
    (folder / "Reference").mkdir(parents=True)
    (folder / "Exams").mkdir()
    reference = folder / "Reference" / "english10_reference.docx"
    reference.write_bytes(word)
    for name in exams:
        (folder / "Exams" / name).write_bytes(word)
    return reference


def run(root, results, admin, checker, **kw):
    called = []

    def factory():
        called.append(True)
        return admin

    code = regtest.run_session(
        root,
        results,
        admin_factory=factory,
        checker=checker,
        expected_password=kw.get("password", SECRET),
        app_env=kw.get("app_env", "dev"),
        data_path=kw.get("data_path", results),
        project_data=kw.get("project_data", results),
        confirm=lambda: kw.get("confirm", True),
        model="test-model",
        typed_password=kw.get("typed", SECRET),
        today="2026-10-11",
    )
    report = (results / "2026-10-11_001" / "regtest_results.txt").read_text(encoding="utf-8")
    return code, called, report


def test_input_discovery_and_filename_labels(tmp_path, word):
    root = tmp_path / "inputs"
    write_tree(root, word, (
        "english10_OK.docx",
        "english10_FORMAT ERROR.docx",
        "english10_STRUCTURAL_ERROR.docx",
        "english10_STRUCTURE-ERROR.docx",
        "~$english10_OK.docx",
    ))
    subjects, errors = regtest.discover(root)
    assert errors == []
    assert [exam.expected for exam in subjects[0].exams] == [
        "FORMATTING", "ALL GOOD", "STRUCTURAL", "STRUCTURAL",
    ]
    assert regtest.expected_status("Math10_ok.docx") == "ALL GOOD"


def test_folders_starting_with_x_are_skipped(tmp_path, word):
    root = tmp_path / "inputs"
    write_tree(root, word, ("english10_OK.docx",))
    skipped_dir = root / "xEnglish 10"
    skipped_dir.mkdir()
    (root / "XMath 10").mkdir()
    skipped = []
    subjects, errors = regtest.discover(root, skipped)
    assert errors == []
    assert [subject.folder_name for subject in subjects] == ["English 10"]
    assert skipped == ["xEnglish 10", "XMath 10"]
    admin = FakeAdmin([
        {"id": "kept", "subject": "xEnglish", "grade": "10", "enabled": True, "version": "abc"},
        {"id": "old", "subject": "Old", "grade": "1", "enabled": True, "version": "abc"},
    ])
    checker = FakeCheck([regtest.CheckOutcome("ALL GOOD", "", 0.1, None)])
    code, called, report = run(root, tmp_path / "results", admin, checker)
    assert code == 0 and called == [True]
    assert admin.deleted == ["old"]
    assert admin.active_reference("xEnglish", "10")["id"] == "kept"
    assert "SKIPPED: XMath 10" in report and "SKIPPED: xEnglish 10" in report
    assert "Total Tests: 1" in report
    assert len(checker.calls) == 1


def test_invalid_filenames_are_rejected(tmp_path, word):
    root = tmp_path / "inputs"
    write_tree(root, word, ("notes.docx", "exam_OK_FORMAT_ERROR.docx"))
    _subjects, errors = regtest.discover(root)
    assert any("no expected-result label" in error for error in errors)
    assert any("conflicting" in error for error in errors)


def test_missing_and_multiple_references(tmp_path, word):
    missing = tmp_path / "missing" / "English 10"
    (missing / "Reference").mkdir(parents=True)
    (missing / "Exams").mkdir()
    _subjects, errors = regtest.discover(missing.parent)
    assert any("no reference DOCX" in error for error in errors)
    multiple = tmp_path / "multiple"
    write_tree(multiple, word)
    extra = multiple / "English 10" / "Reference" / "second.docx"
    extra.write_bytes(word)
    _subjects, errors = regtest.discover(multiple)
    assert any("more than one reference" in error for error in errors)


def test_duplicate_subject_and_invalid_folder(tmp_path, word):
    root = tmp_path / "inputs"
    write_tree(root, word)
    other = root / "english  10"
    (other / "Reference").mkdir(parents=True)
    (other / "Exams").mkdir()
    (other / "Reference" / "english10_reference.docx").write_bytes(word)
    (other / "Exams" / "english10_OK.docx").write_bytes(word)
    plain = root / "English"
    plain.mkdir()
    _subjects, errors = regtest.discover(root)
    assert any("repeats" in error for error in errors)
    assert any("not a valid subject" in error for error in errors)


def test_authentication_failure_does_not_change_references(tmp_path, word):
    root = tmp_path / "inputs"
    write_tree(root, word)
    admin = FakeAdmin([{"id": "old", "subject": "Old", "grade": "1", "enabled": True, "version": "abc"}])
    code, called, report = run(
        root, tmp_path / "results", admin, FakeCheck([]), typed="wrong-password",
    )
    assert code == 1 and called == [] and admin.deleted == []
    assert "ADMIN authentication failed." in report
    assert SECRET not in report and "wrong-password" not in report


def test_cancel_and_production_environment_do_not_delete(tmp_path, word):
    root = tmp_path / "inputs"
    write_tree(root, word)
    admin = FakeAdmin([{"id": "old", "subject": "Old", "grade": "1", "enabled": True, "version": "abc"}])
    code, called, report = run(root, tmp_path / "results", admin, FakeCheck([]), confirm=False)
    assert code == 0 and called == [] and "not changed" in report
    code, called, report = run(
        root, tmp_path / "results2", admin, FakeCheck([]), app_env="prod",
    )
    assert code == 1 and called == [] and "production" in report


def test_delete_load_activate_and_hash(tmp_path, word):
    root = tmp_path / "inputs"
    reference = write_tree(root, word, ("english10_OK.docx", "english10_FORMAT ERROR.docx"))
    admin = FakeAdmin([
        {"id": "old", "subject": "Old", "grade": "1", "enabled": True, "version": "abc"},
    ])
    checker = FakeCheck([
        regtest.CheckOutcome("FORMATTING", "", 1.0, 0.25),
        regtest.CheckOutcome("ALL GOOD", "", 2.0, 0.25),
    ])
    code, called, report = run(root, tmp_path / "results", admin, checker)
    assert code == 0 and called == [True]
    assert admin.deleted == ["old"] and admin.list_references()[0]["enabled"] is True
    assert admin.activated == ["id-English-10"]
    assert admin.added[0][0:2] == ("English", "10")
    assert hashlib.sha256(admin.added[0][2]).hexdigest() == admin.active_reference("English", "10")["version"]
    assert reference.read_bytes() == word
    assert "PASSED: 2" in report and "FAILED: 0" in report
    assert checker.calls[0][2] == "english10_reference.docx"


def test_hash_mismatch_stops_before_checks(tmp_path, word):
    root = tmp_path / "inputs"
    write_tree(root, word)
    admin = FakeAdmin()
    admin.fail = "hash"
    checker = FakeCheck([])
    code, _called, report = run(root, tmp_path / "results", admin, checker)
    assert code == 1 and checker.calls == []
    assert "Verified: FAIL" in report and "INITIALIZATION RESULT: FAIL" in report
    assert "Original references were not restored." in report
    assert "TEST 001" not in report


def test_activation_and_delete_failures(tmp_path, word):
    root = tmp_path / "inputs"
    write_tree(root, word)
    admin = FakeAdmin()
    admin.fail = "activate"
    code, _called, report = run(root, tmp_path / "results", admin, FakeCheck([]))
    assert code == 1 and "Activated: FAIL" in report and "TEST 001" not in report
    admin = FakeAdmin([{"id": "old", "subject": "Old", "grade": "1", "enabled": True, "version": "abc"}])
    admin.fail = "delete"
    code, _called, report = run(root, tmp_path / "results2", admin, FakeCheck([]))
    assert code == 1 and "Reference deletion failed." in report
    assert admin.added == []


def test_comparison_system_error_and_continued_failures(tmp_path, word):
    root = tmp_path / "inputs"
    write_tree(root, word, ("english10_OK.docx", "english10_STRUCTURAL ERROR.docx"))
    checker = FakeCheck([
        regtest.CheckOutcome("SYSTEM ERROR", "The check timed out.", 3.0, None),
        regtest.CheckOutcome("FORMATTING", "A section is missing.", 1.0, 0.1),
    ])
    code, _called, report = run(root, tmp_path / "results", FakeAdmin(), checker)
    assert code == 1
    assert regtest.judge("ALL GOOD", "ALL GOOD") == "PASS"
    assert regtest.judge("FORMATTING", "STRUCTURAL") == "FAIL"
    assert regtest.judge("STRUCTURAL", "SYSTEM ERROR") == "FAIL"
    assert "Actual: SYSTEM ERROR" in report and "The check timed out." in report
    assert "Actual: FORMATTING" in report and "Result: FAIL" in report
    assert "PASSED: 0" in report and "FAILED: 2" in report
    assert "Estimated API Cost: not available" in report
    assert len(checker.calls) == 2


def test_interrupt_does_not_mark_remaining_tests_passed(tmp_path, word):
    root = tmp_path / "inputs"
    write_tree(root, word, ("english10_OK.docx", "english10_FORMAT ERROR.docx"))
    checker = FakeCheck([KeyboardInterrupt()])
    code, _called, report = run(root, tmp_path / "results", FakeAdmin(), checker)
    assert code == 2
    assert "Run interrupted." in report
    assert "TEST 001" not in report
    assert "PASSED: 0" in report


def test_report_folders_are_not_overwritten(tmp_path, word):
    results = tmp_path / "results"
    first = regtest.allocate_run_dir(results, "2026-10-11")
    second = regtest.allocate_run_dir(results, "2026-10-11")
    assert first.name == "2026-10-11_001"
    assert second.name == "2026-10-11_002"
    (first / "regtest_results.txt").write_text("keep", encoding="utf-8")
    root = tmp_path / "inputs"
    write_tree(root, word)
    code, _called, _report = run(root, results, FakeAdmin(), FakeCheck([
        regtest.CheckOutcome("ALL GOOD", "", 0.2, 0.5),
    ]))
    assert code == 0
    assert (first / "regtest_results.txt").read_text(encoding="utf-8") == "keep"
    saved = (results / "2026-10-11_003" / "regtest_results.txt").read_text(encoding="utf-8")
    assert "TEST 001" in saved and "english10_OK.docx" in saved


def test_process_exit_code(monkeypatch):
    monkeypatch.setattr(regtest, "run_session", lambda *_args, **_kwargs: 1)
    assert regtest.main() == 1


def test_production_check_uses_check_and_reports_system_error(monkeypatch, tmp_path, word):
    reference = tmp_path / "reference.docx"
    exam = tmp_path / "exam.docx"
    reference.write_bytes(word)
    exam.write_bytes(word)
    calls = []

    def fake_run(kind, subject, grade, reference_path, exam_path, progress, **kwargs):
        calls.append(kind)
        return job.Result("OK", "")

    monkeypatch.setattr(regtest.job, "run", fake_run)
    outcome = regtest.production_check("English", "10", reference, exam)
    assert calls == ["check"] and outcome.status == "ALL GOOD" and outcome.cost is None

    monkeypatch.setattr(regtest.job, "run", lambda *args, **kwargs: job.Result("SYSTEM ERROR", "timed out", error_code="timeout"))
    failed = regtest.production_check("English", "10", reference, exam)
    assert failed.status == "SYSTEM ERROR"
    assert regtest.judge("ALL GOOD", failed.status) == "FAIL"


def test_real_admin_reset_load_activate_and_hash(tmp_path, word):
    saved = (records.DATA, records.REFERENCES, records.INDEX, records.LOG, records.USAGE)
    try:
        settings = Settings(data=tmp_path / "appdata", admin_password=SECRET, app_env="dev")
        service = Service(settings)
        service.save_reference("Old", "1", word, enabled=True)
        admin = regtest.ProductionAdmin(service)
        root = tmp_path / "inputs"
        reference = write_tree(root, word)
        outcome = regtest.initialize([regtest.discover(root)[0][0]], admin)
        assert outcome.ok and outcome.deleted == 1
        active = admin.active_reference("English", "10")
        assert active["enabled"] is True
        assert active["version"] == hashlib.sha256(word).hexdigest()
        assert reference.read_bytes() == word
        assert admin.active_reference("Old", "1") is None
        assert [ref["subject"] for ref in admin.list_references()] == ["English"]
    finally:
        records.DATA, records.REFERENCES, records.INDEX, records.LOG, records.USAGE = saved
