"""Acceptance checks for the fixed Teacher Exam English 10 Actual.docx."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from run_core import run_pipeline


def _majors(blocks):
    found = [b for b in blocks if b.block_type == "major_question"]
    return sorted(found, key=lambda b: b.source_order)


def _kids(blocks, parent_id, block_type=None):
    rows = [b for b in blocks if b.parent_id == parent_id]
    rows.sort(key=lambda b: (b.source_order, b.source_id.startswith("img")))
    if block_type:
        rows = [b for b in rows if b.block_type == block_type]
    return rows


def test_teacher_exam():
    result = run_pipeline(
        ROOT / "input" / "Teacher Exam English 10 Actual.docx",
        ROOT / "input" / "Reference Exam English 10 WORD.docx",
        ROOT / "output",
    )
    blocks = result["blocks"]
    assert result["problems"] == [], result["problems"]
    assert all(ok for _, ok, _ in result["checks"])
    assert result["review"] == []

    majors = _majors(blocks)
    assert len(majors) == 16
    assert [b.final_label.split()[0] for b in majors] == [f"Q{n})" for n in range(1, 17)]
    assert majors[0].original_text.lstrip().startswith("1.")
    assert "Section" in next(b.original_text for b in blocks if b.block_type == "section")

    pre = [b for b in blocks if b.list_level is not None]
    assert pre
    assert all(b.block_type == "syllabus" for b in pre)
    assert all(b.final_label == "-" for b in pre)
    assert all(not b.final_label.startswith("Q") for b in blocks if b.block_type in {"metadata", "syllabus", "other"})

    q9 = majors[8]
    assert "9." in q9.original_text
    assert q9.branch_carrier
    branches = _kids(blocks, q9.source_id, "branch")
    assert len(branches) == 1
    assert branches[0].original_text.lstrip().startswith("(B)")
    a_subs = _kids(blocks, q9.source_id, "subquestion")
    b_subs = _kids(blocks, branches[0].source_id, "subquestion")
    assert [b.final_label for b in a_subs] == ["i)", "ii)", "iii)"]
    assert [b.final_label for b in b_subs] == ["i)", "ii)", "iii)"]
    assert _kids(blocks, a_subs[0].source_id, "choice")
    assert not any(b.block_type == "major_question" and b.original_text.lstrip().startswith("1.At") for b in blocks)

    q10 = majors[9]
    subs = _kids(blocks, q10.source_id, "subquestion")
    assert [b.final_label for b in subs] == ["i)", "ii)", "iii)", "iv)"]
    for sub in subs:
        choices = _kids(blocks, sub.source_id, "choice")
        assert choices
        assert all(c.block_type == "choice" for c in choices)
    iv_kids = [b for b in blocks if b.parent_id == subs[3].source_id]
    assert any(b.block_type == "continuation" and "raindrops" in b.original_text for b in iv_kids)
    assert any(b.block_type == "choice" and "Diamond" in b.original_text for b in iv_kids)

    for major, count in ((majors[10], 7), (majors[11], 3), (majors[12], 3)):
        subs = _kids(blocks, major.source_id, "subquestion")
        assert len(subs) == count
        assert all(b.final_label.endswith(")") for b in subs)
        assert all(not _kids(blocks, b.source_id, "choice") for b in subs)

    for major in majors[13:]:
        assert _kids(blocks, major.source_id, "or_marker") or "OR" in major.original_text
        alts = _kids(blocks, major.source_id, "alternative")
        assert len(alts) == 1
        assert alts[0].block_type == "alternative"

    q6 = majors[5]
    assert "pictures" in q6.original_text
    images = [b for b in blocks if b.block_type == "image"]
    assert len(images) == 1
    assert images[0].host_paragraph_id == q6.source_id
    assert images[0].parent_id == q6.source_id
    assert images[0].media_part
