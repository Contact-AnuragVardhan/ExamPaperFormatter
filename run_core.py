"""Run CORE Phase 1 on the fixed teacher exam and the reference exam."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.discover import discover, reference_convention_notes
from core.extract import extract_document
from core.integrity import integrity
from core.models import clone_blocks
from core.number import labels_are_stable
from core.validate import validate


def _default_input(name: str) -> Path:
    return ROOT / "input" / name


def _line(block) -> str:
    parent = block.parent_id or "-"
    label = block.final_label or "-"
    text = block.original_text.replace("\n", " ")
    return (
        f"{block.source_id} | {block.block_type:<16} | {parent:<8} | {label:<16} | {text}"
    )


def _tree_lines(blocks) -> list[str]:
    by_parent: dict[str | None, list] = {}
    ordered = sorted(
        blocks,
        key=lambda b: (b.source_order, 0 if b.source_id.startswith("p") else 1, b.source_id),
    )
    for block in ordered:
        by_parent.setdefault(block.parent_id, []).append(block)

    lines: list[str] = []

    def walk(node, depth: int) -> None:
        label = node.final_label if node.final_label and node.final_label != "-" else node.block_type
        text = node.original_text.replace("\n", " ")
        if len(text) > 140:
            text = text[:137] + "..."
        lines.append(f"{'  ' * depth}{node.source_id} {label} {text}".rstrip())
        for child in by_parent.get(node.source_id, []):
            walk(child, depth + 1)

    roots = [b for b in ordered if not b.parent_id]
    for root in roots:
        walk(root, 0)
    return lines


def run_pipeline(teacher: Path, reference: Path, out_dir: Path) -> dict:
    source = extract_document(teacher)
    reference_blocks = extract_document(reference)
    notes = reference_convention_notes(reference_blocks)
    blocks = clone_blocks(source)
    discover(blocks, notes)
    stable = labels_are_stable(blocks)
    problems = validate(blocks, stable)
    checks = integrity(source, blocks)

    out_dir.mkdir(parents=True, exist_ok=True)
    payload = [b.to_dict() for b in blocks]
    (out_dir / "hierarchy.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    flat = ["SOURCE | TYPE | PARENT | FINAL LABEL | TEXT", *(_line(b) for b in sorted(
        blocks,
        key=lambda b: (b.source_order, 0 if b.source_id.startswith("p") else 1, b.source_id),
    ))]
    tree = ["", "TREE", *(_tree_lines(blocks))]
    (out_dir / "hierarchy.txt").write_text("\n".join(flat + tree) + "\n", encoding="utf-8")

    review = [b for b in blocks if b.review_required]
    integrity_lines = ["INTEGRITY"]
    for name, ok, detail in checks:
        integrity_lines.append(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
    integrity_lines.append("")
    integrity_lines.append("VALIDATION")
    if problems:
        integrity_lines.extend(f"FAIL {item}" for item in problems)
    else:
        integrity_lines.append("PASS structural invariants")
    integrity_lines.append("")
    integrity_lines.append(f"NUMBERING_STABLE {'PASS' if stable else 'FAIL'}")
    integrity_lines.append(f"REVIEW_REQUIRED {len(review)}")
    for block in review:
        integrity_lines.append(
            f"{block.source_id} | {block.block_type} | {block.parent_id} | "
            f"{block.confidence} | {block.reason} | {block.original_text[:120]}"
        )
    integrity_lines.append("")
    integrity_lines.append("REFERENCE " + notes)
    (out_dir / "integrity.txt").write_text("\n".join(integrity_lines) + "\n", encoding="utf-8")
    (out_dir / "reference_overview.txt").write_text(
        "Structural counts only. Reference wording is not stored.\n" + notes + "\n",
        encoding="utf-8",
    )

    ok = not problems and all(flag for _, flag, _ in checks)
    return {
        "ok": ok,
        "blocks": blocks,
        "problems": problems,
        "checks": checks,
        "review": review,
        "reference_notes": notes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CORE Phase 1 exam hierarchy")
    parser.add_argument("--teacher", type=Path, default=_default_input("Teacher Exam English 10 Actual.docx"))
    parser.add_argument("--reference", type=Path, default=_default_input("Reference Exam English 10 WORD.docx"))
    parser.add_argument("--output", type=Path, default=ROOT / "output")
    args = parser.parse_args(argv)
    result = run_pipeline(args.teacher, args.reference, args.output)
    majors = [b for b in result["blocks"] if b.block_type == "major_question"]
    majors.sort(key=lambda b: b.source_order)
    print(f"majors {len(majors)}")
    for block in majors:
        print(f"{block.final_label} {block.source_id} {block.original_text[:80]}")
    print(f"review {len(result['review'])}")
    for name, ok, detail in result["checks"]:
        print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
    if result["problems"]:
        print(f"validation problems {len(result['problems'])}")
        for item in result["problems"][:30]:
            print(item)
    print("PASS" if result["ok"] else "FAIL")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
