"""KISS exam statuses: ALL GOOD, FORMATTING, and STRUCTURAL."""
from pathlib import Path

import job
import records
import reporting
from reporting import Finding, assess, assess_engine, requirements_for
from test_integration import submit

ROOT = Path(__file__).resolve().parents[1]
READY = "Your exam is ready. You can proceed."
FORMAT = "Some formatting needs attention. You can proceed."


def structural_message(*issues):
    bullets = "\n".join(f"- {issue}" for issue in issues)
    return f"Please fix these before continuing:\n{bullets}\n\nUpload the corrected exam."


def test_no_errors_are_all_good_and_proceed():
    report = assess(())
    assert report.status == "ALL GOOD" and report.proceed and report.message == READY
    assert assess_engine("OK").message == READY
    assert assess_engine("PASS").message == READY


def test_formatting_errors_only_proceed():
    report = assess((Finding("The font does not match.", "formatting"),))
    assert report.status == "FORMATTING" and report.proceed and report.message == FORMAT
    assert "font" not in report.message


def test_structural_errors_only_block():
    report = assess((Finding("The answer key is missing.", "structural"),))
    assert report.status == "STRUCTURAL" and not report.proceed
    assert report.message == structural_message("The answer key is missing.")


def test_both_types_show_only_structural_and_block():
    report = assess((
        Finding("The font does not match.", "formatting"),
        Finding("The answer key is missing.", "structural"),
    ))
    assert report.status == "STRUCTURAL" and not report.proceed
    assert report.message == structural_message("The answer key is missing.")
    assert "font" not in report.message
    assert report.formatting == ("The font does not match.",)


def test_missing_header_section_or_image_needs_the_selected_reference():
    for element, text in (
        ("header", "The header is missing."),
        ("section", "A section is missing."),
        ("image", "An image is missing."),
    ):
        unjustified = assess((Finding(text, "structural", element=element, confirmed=True),))
        assert unjustified.proceed and unjustified.status != "STRUCTURAL"
        justified = assess((Finding(text, "structural", element=element, confirmed=True, required_by_reference=True),))
        assert justified.status == "STRUCTURAL" and not justified.proceed


def test_uncertain_findings_are_not_structural_failures():
    report = assess((Finding("The section might be missing.", "structural", element="section", confirmed=False),))
    assert report.status == "ALL GOOD" and report.proceed
    assert "might" not in report.message
    assert report.withheld == ("The section might be missing.",)


def test_reference_requirements_stay_with_the_selected_reference():
    finding = Finding("A section is missing.", "structural", element="section", confirmed=True)
    by_reference = {"english-10": {"section"}, "math-10": ()}
    blocked = assess((finding,), requirements_for("english-10", by_reference))
    allowed = assess((finding,), requirements_for("math-10", by_reference))
    assert blocked.status == "STRUCTURAL" and not blocked.proceed
    assert allowed.proceed and allowed.status != "STRUCTURAL"
    swapped = {"english-10": (), "math-10": {"section"}}
    assert assess((finding,), requirements_for("english-10", swapped)).proceed
    assert not assess((finding,), requirements_for("math-10", swapped)).proceed
    source = (ROOT / "reporting.py").read_text(encoding="utf-8").casefold()
    for word in ("english", "chemistry", "mathematics", "physics", "biology", "math"):
        assert word not in source


def test_formatter_status_is_not_upgraded_or_diluted():
    formatting = assess_engine("FORMAT ERROR", "The answer key is missing.")
    assert formatting.status == "FORMATTING" and formatting.proceed
    mixed = assess_engine(
        "STRUCTURAL ERROR",
        "The answer key is missing and cannot be completed without adding academic content.\n"
        "The font does not match the reference.",
    )
    assert mixed.status == "STRUCTURAL" and not mixed.proceed
    assert "font" not in mixed.message and "answer key" in mixed.message
    image = assess_engine("STRUCTURAL ERROR", "The image is missing.")
    assert image.proceed and image.status != "STRUCTURAL"
    required = assess_engine("STRUCTURAL ERROR", "The reference requires an image and it is missing.")
    assert required.status == "STRUCTURAL" and not required.proceed
    uncertain = assess_engine("STRUCTURAL ERROR", "The header might be missing.")
    assert uncertain.proceed and uncertain.status != "STRUCTURAL"


def _finish(svc, word, status, detail, kind="check", channel="web", owner="web:report", write_output=True):
    def engine(*_args, **kw):
        path = None
        if write_output:
            path = kw["output_dir"] / "returned.docx"
            path.write_bytes(word)
        return job.Result(status, detail, path)
    svc.engine = engine
    row = submit(svc, word, kind=kind, idem=f"{channel}-{kind}-{status}-{owner}", owner=owner, channel=channel)
    svc.process_one()
    return svc.get(row["id"])


def test_service_and_whatsapp_share_the_four_results(env, word):
    svc, _, meta = env
    clear = _finish(svc, word, "OK", "", kind="check", write_output=False)
    assert clear["state"] == "completed" and clear["result_status"] == "ALL GOOD" and clear["message"] == READY

    formatting = _finish(svc, word, "FORMAT ERROR", "The font does not match the reference.", kind="check", owner="web:format")
    assert formatting["state"] == "completed" and formatting["result_status"] == "FORMATTING"
    assert formatting["message"] == FORMAT and "font" not in formatting["message"]
    assert Path(formatting["output"]).read_bytes() == word

    structural = _finish(
        svc, word, "STRUCTURAL ERROR", "The answer key is missing.",
        kind="format", owner="web:structural",
    )
    assert structural["state"] == "needs_changes" and structural["result_status"] == "STRUCTURAL"
    assert structural["message"] == structural_message("The answer key is missing.")
    assert Path(structural["output"]).read_bytes() == word

    both = _finish(
        svc, word, "STRUCTURAL ERROR",
        "The answer key is missing and cannot be completed without adding academic content.\n"
        "The font does not match the reference.",
        kind="check", owner="wa:15551230000", channel="whatsapp",
    )
    assert both["state"] == "needs_changes" and both["result_status"] == "STRUCTURAL"
    assert both["message"] == structural_message(
        "The answer key is missing and cannot be completed without adding academic content."
    )
    assert "font" not in both["message"]
    assert "font" in records.read_log()
    for _ in range(10):
        if not svc.deliver_one():
            break
    assert meta.documents[-1][3] == "Issues report (not a formatted exam).\n" + both["message"]
