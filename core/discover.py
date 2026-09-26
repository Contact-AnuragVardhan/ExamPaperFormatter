"""Whole-document hierarchy discovery.

One structured model call sees every extracted block and returns only
type, parent, confidence, and reason for existing source IDs.
It does not rewrite academic text and does not assign final labels.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from openai import OpenAI

from core.models import BLOCK_TYPES, Block

_CHOICE_MARK = re.compile(r"([A-Da-d])\s*[\.\)]")


def choice_slots(text: str) -> int:
    """How many sequential choice markers one choice block contains.

    The run may start at a, or at a later letter when the teacher split
    the options across paragraphs. Glued markers such as 'wordb.' still count.
    """
    found: list[str] = []
    for match in _CHOICE_MARK.finditer(text or ""):
        letter = match.group(1).lower()
        prev = text[match.start() - 1] if match.start() else ""
        if not found:
            if letter not in "abcd":
                continue
        elif letter != chr(ord(found[-1]) + 1):
            continue
        elif prev.isalpha() and letter == "a":
            continue
        found.append(letter)
        if letter == "d":
            break
    return max(1, len(found))


def reference_convention_notes(blocks: list[Block]) -> str:
    """Structural counts only. No reference-exam wording is returned."""
    section_n = 0
    q_n = 0
    roman_n = 0
    choice_n = 0
    image_n = 0
    or_n = 0
    for block in blocks:
        if block.source_id.startswith("img"):
            image_n += 1
            continue
        text = block.original_text.strip()
        if re.fullmatch(r"Section\s+[A-D]", text, flags=re.I):
            section_n += 1
        if re.match(r"Q\d+", text):
            q_n += 1
        if re.match(r"\([ivx]+\)", text, flags=re.I):
            roman_n += 1
        if re.match(r"[A-D]\)", text):
            choice_n += 1
        if text == "OR":
            or_n += 1
    return (
        f"paragraphs={sum(1 for b in blocks if b.source_id.startswith('p'))} "
        f"images={image_n} section_headings={section_n} "
        f"q_prefixed_lines={q_n} parenthesized_roman_lines={roman_n} "
        f"uppercase_choice_lines={choice_n} standalone_OR_lines={or_n}"
    )


_SYSTEM = """You discover the hierarchy of ONE teacher exam.

You receive every block in document order. Read the whole list before you classify anything.
Return STRICT JSON only. Do not rewrite, correct, translate, or invent academic text.
Every input source_id must appear exactly once in your output.
parent_id must be an existing source_id or null.
You assign structure only. Do not assign Q / i / a labels. A later program numbers the tree.

Block types:
metadata, syllabus, section, major_question, branch, passage, subquestion, choice, alternative, or_marker, continuation, image, other

Decision procedure, applied to the whole document:

1. The exam body begins at the first section heading (a line that is only "Section A/B/C/D", ignoring spaces and hyphens).
2. Everything before that heading is pre-exam. It is never a major question.
   Word list numbering (list_level) in that region means syllabus.
   Other non-empty pre-exam lines are metadata. Empty lines are other.
3. Section headings are type section, parent null.
4. Major questions are the paper's single increasing sequence 1, 2, 3, ... in document order.
   Choose them only after seeing every leading integer in the exam.
   A restarted 1, 2, 3... nested inside an earlier question is NOT the next major question.
   The first "1" of the exam is the first major. After major k, skip any inner run that restarts at 1 and climbs 2, 3, 4... and take the later paragraph whose leading integer is k+1.
   A leading integer may be "9.(a)" or "1." or, rarely, "1" plus a space and no period.
   Do not hard-code how many majors exist. The sequence ends when the next integer is not in the document.
5. A major stem that itself opens a branch, such as a trailing "(a)" on the question number, is still ONE major question. Set branch_carrier true on that block. Its text is branch A. A later sibling branch block is the next branch, not a new major.
6. A branch block is a lettered item that introduces its own passage and/or a restarted inner question list. Short lettered options that are followed by the next letter are NOT branches.
7. Under a major or branch:
   - Unmarked paragraphs before the first inner question are passage. Several poem lines are several passage blocks.
   - The outer restarted marker family (1. 2. 3. or i. ii. iii.) contains subquestions.
   - A deeper letter family under those items contains choices. One source line may hold several choices; keep it as ONE choice block.
   - A letter glued to the previous word (for example "...b.") is still a choice marker on that same line, not a new subquestion.
   - If the only child family is letters and the items are tasks (blanks, imperatives, questions), they are subquestions.
   - If the only child family is letters and the items are short topic options under a choose-one stem, they are choices.
   - Roman-numeral items with no choices under them are subquestions.
   - An unmarked line that continues the open subquestion is continuation, parented to that subquestion.
8. OR is uppercase and standalone, or it is the last word of a major stem.
   The major's own text is the first alternative. Do not create an extra block for it.
   A standalone OR line is or_marker. The following alternative paragraph is type alternative.
   Both parent to that major question. Do not turn the alternative into a new major.
9. An image block parents to host_paragraph_id. Do not drop it. Do not treat a horizontal rule as an image.
10. Parents:
    major_question, section, metadata, syllabus, other -> null
    branch, passage, alternative, or_marker -> the major, except a passage that sits inside a later branch parents to that branch
    subquestion -> the open branch if one is open, otherwise the major
    choice -> the open subquestion, or the major when the choices are direct topic options
    continuation -> the block it continues
11. If a relationship is genuinely impossible to resolve, set review_required true and still give your best type and parent. Do not use review_required merely because the teacher's own numbers are inconsistent.
12. confidence is from 0 to 1. reason is one short sentence and must not contain a rewritten question.

branch_carrier is true only for a major_question whose own paragraph opens the first branch. Otherwise false.
"""


def _payload(blocks: list[Block]) -> str:
    lines = []
    for block in blocks:
        flags = []
        if block.list_level is not None:
            flags.append(f"list_level={block.list_level}")
        if block.media_part:
            flags.append(f"image={block.media_part}")
        if block.host_paragraph_id:
            flags.append(f"host={block.host_paragraph_id}")
        flag = ",".join(flags)
        text = block.original_text.replace("\t", " ").replace("\n", " ")
        lines.append(f"{block.source_id}\t{flag}\t{text}")
    return "\n".join(lines)


_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "source_id": {"type": "string"},
                    "block_type": {"type": "string", "enum": list(BLOCK_TYPES)},
                    "parent_id": {"type": ["string", "null"]},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                    "review_required": {"type": "boolean"},
                    "branch_carrier": {"type": "boolean"},
                },
                "required": [
                    "source_id",
                    "block_type",
                    "parent_id",
                    "confidence",
                    "reason",
                    "review_required",
                    "branch_carrier",
                ],
            },
        }
    },
    "required": ["blocks"],
}


def _call_model(user_text: str) -> dict:
    client = OpenAI()
    response = client.chat.completions.create(
        model=os.environ.get("EXAM_REDO_MODEL", "gpt-4.1"),
        temperature=0,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user_text},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "exam_hierarchy",
                "strict": True,
                "schema": _SCHEMA,
            },
        },
    )
    content = response.choices[0].message.content or "{}"
    return json.loads(content)


def discover(blocks: list[Block], reference_notes: str) -> list[Block]:
    """Classify every block. Original text is never replaced."""
    by_id = {b.source_id: b for b in blocks}
    user = (
        "Reference-exam structural counts (wording omitted):\n"
        f"{reference_notes}\n\n"
        "Teacher-exam blocks. Columns: source_id, extraction flags, original text.\n"
        f"{_payload(blocks)}"
    )
    data = _call_model(user)
    returned = {item["source_id"]: item for item in data.get("blocks", [])}

    missing = [sid for sid in by_id if sid not in returned]
    if missing:
        repair = (
            user
            + "\n\nYour previous answer omitted these source_ids: "
            + ", ".join(missing)
            + ". Return the FULL list again, one record for every source_id, including these."
        )
        data = _call_model(repair)
        returned = {item["source_id"]: item for item in data.get("blocks", [])}

    known = set(by_id)
    for block in blocks:
        item = returned.get(block.source_id)
        if item is None:
            block.block_type = "other"
            block.parent_id = None
            block.confidence = 0.0
            block.reason = "Discovery returned no record for this source block."
            block.review_required = True
            block.branch_carrier = False
            continue
        block_type = item["block_type"]
        parent_id = item["parent_id"]
        if block_type not in BLOCK_TYPES:
            block.review_required = True
            block_type = "other"
        if parent_id is not None and parent_id not in known:
            block.review_required = True
            parent_id = None
            block.reason = "Discovery named a parent that is not a source block. " + item.get("reason", "")
        else:
            block.reason = item.get("reason") or ""
        if item.get("review_required"):
            block.review_required = True
        block.block_type = block_type
        # Sections are markers. Major questions are roots, matching the
        # diagnostic model, even if the model hangs them under a section.
        if block_type == "major_question":
            parent_id = None
        block.parent_id = parent_id
        try:
            block.confidence = float(item.get("confidence") or 0.0)
        except (TypeError, ValueError):
            block.confidence = 0.0
        block.branch_carrier = bool(item.get("branch_carrier")) and block.block_type == "major_question"
        if block.block_type == "choice":
            block.slots = choice_slots(block.original_text)
        else:
            block.slots = 1
        # Image blocks must keep their extracted identity even if the model drifts.
        if block.media_part and block.block_type != "image":
            block.block_type = "image"
            block.review_required = True
            block.reason = "Model did not keep this embedded image as an image block. " + block.reason
        if block.host_paragraph_id and block.parent_id != block.host_paragraph_id:
            # The anchor paragraph is evidence from extraction, not a guessed label.
            if block.host_paragraph_id in known:
                block.parent_id = block.host_paragraph_id
    return blocks


def load_cached_discovery(path: Path, blocks: list[Block]) -> list[Block]:
    """Apply a previously saved discovery record. Used only by tests of numbering."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return discover_from_records(blocks, data["blocks"])


def discover_from_records(blocks: list[Block], records: list[dict]) -> list[Block]:
    returned = {item["source_id"]: item for item in records}
    known = {b.source_id for b in blocks}
    for block in blocks:
        item = returned[block.source_id]
        block.block_type = item["block_type"]
        block.parent_id = item["parent_id"] if item["parent_id"] in known or item["parent_id"] is None else None
        block.confidence = float(item["confidence"])
        block.reason = item["reason"]
        block.review_required = bool(item["review_required"])
        block.branch_carrier = bool(item["branch_carrier"]) and block.block_type == "major_question"
        block.slots = choice_slots(block.original_text) if block.block_type == "choice" else 1
    return blocks
