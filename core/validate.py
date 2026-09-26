"""Structural checks that do not depend on a fixed question count."""

from __future__ import annotations

import re

from core.models import Block

_MAJOR_NUM = re.compile(r"^\s*(\d{1,2})[\.\)\s]")


def _map(blocks: list[Block]) -> dict[str, Block]:
    return {b.source_id: b for b in blocks}


def _leading_int(text: str) -> int | None:
    match = _MAJOR_NUM.match(text or "")
    if not match:
        return None
    return int(match.group(1))


def validate(blocks: list[Block], numbering_stable: bool) -> list[str]:
    """Return a list of problem statements. Empty means the invariants hold."""
    problems: list[str] = []
    by_id = _map(blocks)
    ids = [b.source_id for b in blocks]
    if len(ids) != len(set(ids)):
        problems.append("A source id appears more than once.")

    paragraph_ids = [b.source_id for b in blocks if b.source_id.startswith("p")]
    expected = [f"p{i:04d}" for i in range(1, len(paragraph_ids) + 1)]
    if sorted(paragraph_ids) != expected:
        problems.append("A body paragraph is missing or an id was skipped.")

    roots = {"metadata", "syllabus", "section", "major_question", "other"}
    numbered_children = {"subquestion", "choice", "branch", "alternative", "or_marker"}
    first_section_order = None
    for block in blocks:
        if block.block_type == "section" and first_section_order is None:
            first_section_order = block.source_order

    for block in blocks:
        if not block.block_type:
            problems.append(f"{block.source_id} has no type.")
            continue
        if block.review_required:
            problems.append(
                f"REVIEW_REQUIRED {block.source_id} type={block.block_type} "
                f"parent={block.parent_id} confidence={block.confidence} reason={block.reason}"
            )
        parent = by_id.get(block.parent_id) if block.parent_id else None
        if block.parent_id and parent is None:
            problems.append(f"{block.source_id} parent {block.parent_id} does not exist.")
        if block.block_type in numbered_children and parent is None:
            problems.append(f"{block.source_id} ({block.block_type}) has no parent.")
        if block.block_type == "syllabus" and block.parent_id:
            if parent is None or parent.block_type != "syllabus":
                problems.append(f"{block.source_id} syllabus parent is not syllabus.")
        elif block.block_type in roots and block.parent_id:
            problems.append(f"{block.source_id} ({block.block_type}) should be a root.")
        if block.block_type == "choice" and (
            parent is None or parent.block_type not in {"subquestion", "major_question", "branch"}
        ):
            problems.append(f"{block.source_id} choice parent is not a question or subquestion.")
        if block.block_type == "branch" and (parent is None or parent.block_type != "major_question"):
            problems.append(f"{block.source_id} branch does not belong to a major question.")
        if block.block_type in {"alternative", "or_marker"} and (
            parent is None or parent.block_type != "major_question"
        ):
            problems.append(f"{block.source_id} OR structure does not belong to a major question.")
        if block.block_type == "subquestion" and (
            parent is None or parent.block_type not in {"major_question", "branch"}
        ):
            problems.append(f"{block.source_id} subquestion parent is not a major question or branch.")
        if block.block_type == "major_question" and block.list_level is not None:
            problems.append(f"{block.source_id} is syllabus numbering promoted to a major question.")
        if (
            first_section_order is not None
            and block.source_order < first_section_order
            and block.block_type == "major_question"
        ):
            problems.append(f"{block.source_id} is pre-exam material turned into a major question.")
        if block.media_part and block.block_type != "image":
            problems.append(f"{block.source_id} image was not preserved as an image block.")
        if block.block_type == "image" and not block.media_part:
            problems.append(f"{block.source_id} is an image without a media part.")

    majors = [b for b in blocks if b.block_type == "major_question"]
    majors.sort(key=lambda b: b.source_order)
    orders = [b.source_order for b in majors]
    if orders != sorted(orders):
        problems.append("Major questions are not in document order.")
    leading = [_leading_int(b.original_text) for b in majors]
    if any(n is None for n in leading):
        problems.append("A major question has no leading integer in the source text.")
    elif leading != list(range(1, len(leading) + 1)):
        problems.append(f"Major leading numbers are {leading}, not 1..N in order.")

    images = [b for b in blocks if b.block_type == "image"]
    if not images:
        problems.append("No image block is present.")

    if not numbering_stable:
        problems.append("Numbering the hierarchy twice produced different labels.")

    # Labels exist for every block once numbering has run.
    for block in blocks:
        if block.final_label == "" and not block.review_required:
            problems.append(f"{block.source_id} has no final label.")

    return problems
