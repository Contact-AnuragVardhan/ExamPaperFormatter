"""Build the formatted teacher exam from the frozen Phase 1 hierarchy."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from format.format_integrity import check_format
from format.word_formatter import format_exam
from run_core import run_pipeline

TEACHER = ROOT / "input" / "Teacher Exam English 10 Actual.docx"
REFERENCE = ROOT / "input" / "Reference Exam English 10 WORD.docx"
OUTPUT = ROOT / "output" / "Formatted Teacher Exam English 10.docx"
REPORT = ROOT / "output" / "format_integrity.txt"


def main() -> int:
    result = run_pipeline(TEACHER, REFERENCE, ROOT / "output")
    format_exam(result["blocks"], TEACHER, OUTPUT, REFERENCE)
    problems = check_format(result["blocks"], TEACHER, OUTPUT, REFERENCE)
    lines = ["FORMAT INTEGRITY", "PASS" if not problems else "FAIL"]
    lines.extend(problems or ["No content differences detected."])
    if not result["ok"]:
        lines.append("PHASE1_PIPELINE FAIL")
        lines.extend(result["problems"])
    else:
        lines.append("PHASE1_PIPELINE PASS")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT}")
    print("\n".join(lines))
    return 0 if not problems and result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
