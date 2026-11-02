"""SPELLCHECK command: real corrections, preserved Word objects, and an untouched FORMAT path."""
import base64
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile
import pytest
import job
import spellcheck
from instructions import FORMAT_INSTRUCTIONS
from service.whatsapp import WhatsAppFlow
from service.menus import replies
from test_integration import send
from conftest import login

H = {"X-Exam-Client": "web"}
SENDER = "15551230000"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
ENGLISH = [
    "The studnts will answer Q1.",
    "Q2) Calculate x² + y² = z². (2 marks)",
]
HINDI = [
    "विदयार्थी प्रश्न 1 का उत्तर दें। photosynthesis",
    "प्रश्न 2: x² + y² = z² (2 अंक)",
]


def _xml(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _paragraph(text):
    return f'<w:p><w:r><w:t xml:space="preserve">{_xml(text)}</w:t></w:r></w:p>'


def make_docx(blocks, extra=None, rels=""):
    """Build a minimal Word package. blocks are plain strings or raw XML."""
    body = []
    for block in blocks:
        body.append(_paragraph(block) if not str(block).lstrip().startswith("<") else block)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
        f'<w:body>{"".join(body)}<w:sectPr/></w:body></w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )
    package_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>'
    )
    document_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{rels}</Relationships>'
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w") as package:
        package.writestr("[Content_Types].xml", content_types)
        package.writestr("_rels/.rels", package_rels)
        package.writestr("word/document.xml", document)
        package.writestr("word/_rels/document.xml.rels", document_rels)
        for name, payload in (extra or {}).items():
            package.writestr(name, payload)
    return buffer.getvalue()


def body_paragraphs(data):
    root = ET.fromstring(ZipFile(BytesIO(data)).read("word/document.xml"))
    body = root.find(f"{W}body")
    return [
        "".join(node.text or "" for node in paragraph.iter(f"{W}t"))
        for paragraph in body
        if paragraph.tag == f"{W}p"
    ]


def part(data, name):
    return ZipFile(BytesIO(data)).read(name)


def slices(data, name, pattern):
    return pattern.findall(part(data, name))


def run_menu(env, filename, data):
    svc, _, meta = env
    meta.input = data
    flow = WhatsAppFlow(svc)
    send(flow, "Hi")
    send(flow, "main.spellcheck")
    assert svc.db.session(SENDER, 30)["state"] == "spellcheck_upload"
    send(flow, filename, "document")
    assert svc.db.session(SENDER, 30)["state"] == "processing"
    assert svc.process_one()
    row = svc.db.one("SELECT * FROM jobs WHERE kind='spellcheck' ORDER BY created DESC LIMIT 1")
    assert (svc.s.data / "jobs" / row["id"] / "teacher.docx").read_bytes() == data
    return svc, meta, row


def test_english_spellcheck_menu_reopens_saved_docx(env):
    data = make_docx(ENGLISH)
    svc, meta, row = run_menu(env, "Exam E.docx", data)
    output = Path(row["output"]).read_bytes()
    paragraphs = body_paragraphs(output)
    assert paragraphs[0] == "The students will answer Q1."
    assert paragraphs[1] == "Q2) Calculate x² + y² = z². (2 marks)"
    report = spellcheck.report_path_for(row["output"]).read_text(encoding="utf-8")
    assert "Location: Body paragraph 1" in report
    assert "Original line: The studnts will answer Q1." in report
    assert "Corrected line: The students will answer Q1." in report
    assert "studnts -> students" in report
    assert "Q2) Calculate" not in report
    while svc.deliver_one():
        pass
    names = [item[2] for item in meta.documents]
    assert names[0] == "Exam E_Spellchecked.docx"
    assert names[1] == "Exam E_Spelling_Changes.txt"
    assert b"studnts -> students" in meta.uploads[1]


def test_clipped_hindi_word_does_not_guess_a_blank_heading(tmp_path):
    source = tmp_path / "Hinidi.docx"
    source.write_bytes(make_docx([
        "5. न्यूटन का प्रथम गति निय किससे संबंधित है?",
        "प्रश्न 2. रिक्त स्था की पूर्ति कीजिए।",
        "दूरी 5 मी है।",
    ]))
    before = source.read_bytes()
    result = spellcheck.run(source, tmp_path)
    paragraphs = body_paragraphs(result.docx_path.read_bytes())
    assert source.read_bytes() == before
    assert paragraphs[0] == "5. न्यूटन का प्रथम गति नियम किससे संबंधित है?"
    assert paragraphs[1] == "प्रश्न 2. रिक्त स्था की पूर्ति कीजिए।"
    assert paragraphs[2] == "दूरी 5 मी है।"
    report = result.report_path.read_text(encoding="utf-8")
    assert "निय -> नियम" in report
    assert "स्था" not in report and "मी ->" not in report
    assert any(item.word == "स्था" and item.reason == "uncertain" for item in result.skipped)


def test_hindi_spellcheck_menu_reopens_saved_docx(env):
    data = make_docx(HINDI)
    _, _, row = run_menu(env, "Exam H.docx", data)
    paragraphs = body_paragraphs(Path(row["output"]).read_bytes())
    assert paragraphs[0] == "विद्यार्थी प्रश्न 1 का उत्तर दें। photosynthesis"
    assert paragraphs[1] == "प्रश्न 2: x² + y² = z² (2 अंक)"
    report = spellcheck.report_path_for(row["output"]).read_text(encoding="utf-8")
    assert "Location: Body paragraph 1" in report
    assert "Original line: विदयार्थी प्रश्न 1 का उत्तर दें। photosynthesis" in report
    assert "Corrected line: विद्यार्थी प्रश्न 1 का उत्तर दें। photosynthesis" in report
    assert "विदयार्थी -> विद्यार्थी" in report
    assert "photosynthesis" in paragraphs[0]
    assert "प्रश्न 2:" not in report


def test_windows_command_matches_the_saved_report(tmp_path):
    """The desktop SPELLCHECK button calls this same command."""
    source = tmp_path / "Exam E.docx"
    source.write_bytes(make_docx(ENGLISH))
    before = source.read_bytes()
    result = spellcheck.run(source)
    assert source.read_bytes() == before
    assert body_paragraphs(result.docx_path.read_bytes())[0] == "The students will answer Q1."
    assert body_paragraphs(result.docx_path.read_bytes())[1] == ENGLISH[1]
    report = result.report_path.read_text(encoding="utf-8")
    assert report == (
        "Changed lines: 1\n"
        "Word corrections: 1\n"
        "\n"
        "Location: Body paragraph 1\n"
        "Original line: The studnts will answer Q1.\n"
        "Corrected line: The students will answer Q1.\n"
        "Corrections: studnts -> students\n"
    )
    assert result.api_calls == 0 and result.elapsed_seconds >= 0


def test_repeated_typo_is_reported_twice(tmp_path):
    source = tmp_path / "Twice.docx"
    source.write_bytes(make_docx(["The studnts helped the studnts."]))
    result = spellcheck.run(source, tmp_path)
    text = body_paragraphs(result.docx_path.read_bytes())[0]
    assert text == "The students helped the students."
    assert result.report_path.read_text(encoding="utf-8").count("studnts -> students") == 2


def test_mixed_language_keeps_a_proper_noun(tmp_path):
    source = tmp_path / "Mixed.docx"
    source.write_bytes(make_docx([
        "The studnts will answer Q1.",
        "विदयार्थी प्रश्न 1 का उत्तर दें।",
        "Newton explained photosynthesis.",
    ]))
    result = spellcheck.run(source, tmp_path)
    paragraphs = body_paragraphs(result.docx_path.read_bytes())
    assert paragraphs[0] == "The students will answer Q1."
    assert paragraphs[1] == "विद्यार्थी प्रश्न 1 का उत्तर दें।"
    assert paragraphs[2] == "Newton explained photosynthesis."
    report = result.report_path.read_text(encoding="utf-8")
    assert "Newton" not in report and "photosynthesis" not in report
    assert "studnts -> students" in report and "विदयार्थी -> विद्यार्थी" in report


def test_no_corrections_still_returns_both_files(tmp_path):
    source = tmp_path / "Clean.docx"
    source.write_bytes(make_docx(["The students will answer Q1."]))
    before = source.read_bytes()
    result = spellcheck.run(source, tmp_path)
    assert source.read_bytes() == before
    assert result.docx_path.read_bytes() == before
    assert result.report_path.read_text(encoding="utf-8") == "No confident spelling corrections found.\n"
    assert result.corrections == 0


def test_clear_passage_typos_keep_names_and_uncertain_words(tmp_path):
    source = tmp_path / "Passage.docx"
    source.write_bytes(make_docx([
        "Fingerprins have fascinated scientists.",
        "used by polce departments",
        "Anees met Fischer in Jaipur.",
        "The caet is here.",
        "British organisations and tranquilisers stay.",
        "Q2) Calculate x² + y² = z². (2 marks)",
    ]))
    before = source.read_bytes()
    result = spellcheck.run(source, tmp_path)
    paragraphs = body_paragraphs(result.docx_path.read_bytes())
    assert source.read_bytes() == before
    assert paragraphs[0] == "Fingerprints have fascinated scientists."
    assert paragraphs[1] == "used by police departments"
    assert paragraphs[2] == "Anees met Fischer in Jaipur."
    assert paragraphs[3] == "The caet is here."
    assert paragraphs[4] == "British organisations and tranquilisers stay."
    assert paragraphs[5] == "Q2) Calculate x² + y² = z². (2 marks)"
    report = result.report_path.read_text(encoding="utf-8")
    assert "Fingerprins -> Fingerprints" in report
    assert "polce -> police" in report
    assert "Anees" not in report and "Fischer" not in report and "caet ->" not in report
    assert any(item.word == "caet" and item.reason == "uncertain" for item in result.skipped)


def test_uncertain_word_is_skipped_and_not_reported_as_a_correction(tmp_path):
    source = tmp_path / "Unsure.docx"
    source.write_bytes(make_docx(["The caet is here."]))
    before = source.read_bytes()
    result = spellcheck.run(source, tmp_path)
    assert result.docx_path.read_bytes() == before
    assert body_paragraphs(result.docx_path.read_bytes()) == ["The caet is here."]
    assert result.report_path.read_text(encoding="utf-8") == "No confident spelling corrections found.\n"
    assert any(item.word == "caet" and item.reason == "uncertain" for item in result.skipped)
    assert "caet ->" not in result.report_path.read_text(encoding="utf-8")


def test_word_split_across_runs_is_not_rewritten(tmp_path):
    source = tmp_path / "Split.docx"
    raw = '<w:p><w:r><w:t>The stu</w:t></w:r><w:r><w:t>dnts stayed.</w:t></w:r></w:p>'
    source.write_bytes(make_docx([raw]))
    before = source.read_bytes()
    result = spellcheck.run(source, tmp_path)
    assert result.docx_path.read_bytes() == before
    assert any(item.reason == "formatting boundary" for item in result.skipped)


def test_field_result_and_equation_text_are_not_rewritten(tmp_path):
    field = (
        '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText xml:space="preserve"> DATE </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        '<w:r><w:t>studnts</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
    )
    source = tmp_path / "Field.docx"
    source.write_bytes(make_docx([field]))
    before = source.read_bytes()
    result = spellcheck.run(source, tmp_path)
    assert result.docx_path.read_bytes() == before
    assert b"studnts" in part(result.docx_path.read_bytes(), "word/document.xml")
    assert any(item.reason == "protected text" for item in result.skipped)


def test_equation_image_and_table_are_preserved(tmp_path):
    omml = '<w:p><m:oMath><m:r><m:t>x+1</m:t></m:r></m:oMath></w:p>'
    drawing = (
        '<w:p><w:r><w:drawing><w:inline><a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="picture"><pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:blipFill><a:blip r:embed="rId5"/></pic:blipFill></pic:pic></a:graphicData></a:graphic>'
        '</w:inline></w:drawing></w:r></w:p>'
    )
    table = '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Newton</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    rel = '<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>'
    header = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:p><w:r><w:t>School header</w:t></w:r></w:p></w:hdr>'
    )
    source = tmp_path / "Protected.docx"
    original = make_docx(
        ["The studnts will answer Q1.", omml, drawing, table],
        extra={"word/media/image1.png": PNG, "word/header1.xml": header},
        rels=rel,
    )
    source.write_bytes(original)
    result = spellcheck.run(source, tmp_path)
    updated = result.docx_path.read_bytes()
    assert source.read_bytes() == original
    assert body_paragraphs(updated)[0] == "The students will answer Q1."
    assert part(updated, "word/media/image1.png") == PNG
    assert part(updated, "word/_rels/document.xml.rels") == part(original, "word/_rels/document.xml.rels")
    assert part(updated, "word/header1.xml") == part(original, "word/header1.xml")
    assert b"<m:oMath>" in part(updated, "word/document.xml")
    assert b"<m:t>x+1</m:t>" in part(updated, "word/document.xml")
    assert b"Newton" in part(updated, "word/document.xml")
    assert b'r:embed="rId5"' in part(updated, "word/document.xml")
    with ZipFile(BytesIO(original)) as before, ZipFile(BytesIO(updated)) as after:
        assert before.namelist() == after.namelist()
        for name in before.namelist():
            if name != "word/document.xml":
                assert before.read(name) == after.read(name)
    report = result.report_path.read_text(encoding="utf-8")
    assert "Location: Body paragraph 1" in report
    assert "Table 1" not in report


def test_header_correction_uses_header_location(tmp_path):
    header = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:p><w:r><w:t>The studnts header</w:t></w:r></w:p></w:hdr>'
    )
    source = tmp_path / "Header.docx"
    source.write_bytes(make_docx(["Ready."], extra={"word/header1.xml": header}))
    result = spellcheck.run(source, tmp_path)
    report = result.report_path.read_text(encoding="utf-8")
    assert "Location: Header 1 paragraph 1" in report
    assert "studnts -> students" in report
    assert "The students header" in part(result.docx_path.read_bytes(), "word/header1.xml").decode("utf-8")
    assert part(result.docx_path.read_bytes(), "word/document.xml") == part(source.read_bytes(), "word/document.xml")


def test_table_location_and_failed_run_leaves_previous_output(tmp_path, monkeypatch):
    table = '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>The studnts row</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    source = tmp_path / "Table.docx"
    source.write_bytes(make_docx(["Intro.", table]))
    result = spellcheck.run(source, tmp_path)
    assert "Location: Table 1 / row 1 / cell 1 / paragraph 1" in result.report_path.read_text(encoding="utf-8")
    assert b"The students row" in part(result.docx_path.read_bytes(), "word/document.xml")
    previous_docx = result.docx_path.read_bytes()
    previous_report = result.report_path.read_text(encoding="utf-8")
    original = source.read_bytes()

    def stop(*_args, **_kwargs):
        raise spellcheck.SpellcheckError("stopped before publish")

    monkeypatch.setattr(spellcheck, "_spellcheck_package", stop)
    with pytest.raises(spellcheck.SpellcheckError):
        spellcheck.run(source, tmp_path)
    assert result.docx_path.read_bytes() == previous_docx
    assert result.report_path.read_text(encoding="utf-8") == previous_report
    assert source.read_bytes() == original


def test_spellcheck_does_not_call_the_format_engine(env):
    data = make_docx(ENGLISH)

    def explode(*_args, **_kwargs):
        raise AssertionError("FORMAT must not run during spellcheck")

    svc, _, meta = env
    svc.engine = explode
    meta.input = data
    flow = WhatsAppFlow(svc)
    send(flow, "Hi")
    send(flow, "main.spellcheck")
    send(flow, "Exam E.docx", "document")
    assert svc.process_one()
    row = svc.db.one("SELECT * FROM jobs WHERE kind='spellcheck'")
    assert row["result_status"] == "SPELLCHECK"
    assert row["reference_id"] == ""
    assert "students" in body_paragraphs(Path(row["output"]).read_bytes())[0]


def test_web_spellcheck_returns_both_downloads(env):
    svc, client, _ = env
    login(client)
    data = make_docx(HINDI)
    response = client.post(
        "/api/spellcheck",
        headers=H,
        data={"submission_id": "spell-1"},
        files={"file": ("Exam H.docx", data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "spellcheck"
    assert svc.process_one()
    jid = response.json()["id"]
    docx = client.get(f"/api/jobs/{jid}/download")
    report = client.get(f"/api/jobs/{jid}/spelling-report")
    assert docx.status_code == 200 and report.status_code == 200
    assert body_paragraphs(docx.content)[0].startswith("विद्यार्थी")
    assert "विदयार्थी -> विद्यार्थी" in report.text
    blocked = client.post(
        "/api/jobs",
        headers=H,
        data={"kind": "spellcheck", "reference_id": "none", "reference_version": "none", "submission_id": "nope"},
        files={"file": ("Exam H.docx", data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert blocked.status_code == 400


def test_reference_exams_keep_protected_parts(tmp_path):
    refs = Path(__file__).resolve().parents[1] / "data" / "references"
    for source_path in refs.glob("*.docx"):
        source = tmp_path / source_path.name
        source.write_bytes(source_path.read_bytes())
        before = source.read_bytes()
        result = spellcheck.run(source, tmp_path / source_path.stem)
        assert source.read_bytes() == before
        updated = result.docx_path.read_bytes()
        with ZipFile(BytesIO(before)) as old, ZipFile(BytesIO(updated)) as new:
            assert old.namelist() == new.namelist()
            for name in old.namelist():
                if name == "word/document.xml" or name.startswith("word/header") or name.startswith("word/footer"):
                    old_xml, new_xml = old.read(name), new.read(name)
                    for token in (b"<m:oMath", b"<m:oMathPara", b"<w:drawing", b"<w:instrText", b"<w:object", b"<w:pict"):
                        assert old_xml.count(token) == new_xml.count(token)
                else:
                    assert old.read(name) == new.read(name)


def test_spellcheck_menu_stays_within_whatsapp_limits(env):
    svc, _, _ = env
    flow = WhatsAppFlow(svc)
    send(flow, "Hi")
    payload = flow.main(SENDER)
    options = replies(payload)
    assert any(item["id"] == "main.spellcheck" for item in options)
    assert len(options) <= 10
    assert len(payload["body"]["text"]) <= 1024
    upload = flow.spellcheck_upload_menu()
    assert len(upload["body"]["text"]) <= 1024
    assert len(flow.help_menu()["body"]["text"]) <= 1024


def test_format_instruction_is_not_used_for_spellcheck():
    assert "SPELLCHECK" not in FORMAT_INSTRUCTIONS
    assert job.FORMAT_INSTRUCTIONS is FORMAT_INSTRUCTIONS
