"""Compare canonical blocks with a fresh extraction. Labels may change; text may not."""

from __future__ import annotations

import re

from core.models import Block

_MARK = re.compile(r"\(\s*\d+\s*x\s*\d+\s*=\s*\d+\s*\)|\(\s*\d+\s*\)")
_NUMBER = re.compile(r"\d+")


def _paras(blocks: list[Block]) -> list[Block]:
    return [b for b in blocks if b.source_id.startswith("p")]


def integrity(source: list[Block], canonical: list[Block]) -> list[tuple[str, bool, str]]:
    source_paras = {b.source_id: b.original_text for b in _paras(source)}
    canon_paras = {b.source_id: b.original_text for b in _paras(canonical)}
    checks: list[tuple[str, bool, str]] = []

    same_ids = set(source_paras) == set(canon_paras)
    checks.append(("question_order_blocks_preserved", same_ids, f"{len(canon_paras)} paragraph blocks"))

    text_same = same_ids and all(source_paras[k] == canon_paras[k] for k in source_paras)
    checks.append(("substantive_text", text_same, "original paragraph text is unchanged"))

    def marks(mapping: dict[str, str]) -> dict[str, list[str]]:
        return {sid: _MARK.findall(text) for sid, text in mapping.items()}

    mark_same = marks(source_paras) == marks(canon_paras)
    checks.append(("marks", mark_same, "mark tokens stay on the same source block"))

    def numbers(mapping: dict[str, str]) -> dict[str, list[str]]:
        return {sid: _NUMBER.findall(text) for sid, text in mapping.items()}

    number_same = numbers(source_paras) == numbers(canon_paras)
    checks.append(("numerical_values", number_same, "digit sequences stay on the same source block"))

    source_passages = {
        b.source_id: b.original_text
        for b in canonical
        if b.block_type == "passage"
    }
    passage_ok = all(source_paras.get(sid) == text for sid, text in source_passages.items())
    checks.append(("passages", passage_ok, f"{len(source_passages)} passage blocks unchanged"))

    source_choices = {
        b.source_id: b.original_text
        for b in canonical
        if b.block_type == "choice"
    }
    choice_ok = all(source_paras.get(sid) == text for sid, text in source_choices.items())
    checks.append(("choices", choice_ok, f"{len(source_choices)} choice blocks unchanged"))

    source_images = [(b.source_id, b.media_part, b.host_paragraph_id) for b in source if b.media_part]
    canon_images = [(b.source_id, b.media_part, b.host_paragraph_id) for b in canonical if b.media_part]
    checks.append((
        "image_presence",
        source_images == canon_images and len(canon_images) > 0,
        f"{len(canon_images)} image block(s)",
    ))

    majors = [b for b in canonical if b.block_type == "major_question"]
    majors.sort(key=lambda b: b.source_order)
    in_order = [b.source_order for b in majors] == sorted(b.source_order for b in majors)
    checks.append(("question_order", in_order and len(majors) > 0, f"{len(majors)} major questions in order"))
    return checks
