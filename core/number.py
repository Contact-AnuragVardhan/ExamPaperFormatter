"""Deterministic labels from an already discovered hierarchy."""

from __future__ import annotations

from core.models import Block


def _roman(n: int) -> str:
    values = (
        (10, "x"),
        (9, "ix"),
        (5, "v"),
        (4, "iv"),
        (1, "i"),
    )
    out = []
    for value, glyph in values:
        while n >= value:
            out.append(glyph)
            n -= value
    return "".join(out)


def _letter(n: int) -> str:
    # 1 -> a, 2 -> b, ... 26 -> z, then aa if ever needed.
    chars = []
    while n > 0:
        n, rem = divmod(n - 1, 26)
        chars.append(chr(ord("a") + rem))
    return "".join(reversed(chars))


def _branch_letter(n: int) -> str:
    return chr(ord("A") + n - 1)


def _by_id(blocks: list[Block]) -> dict[str, Block]:
    return {b.source_id: b for b in blocks}


def _children(blocks: list[Block]) -> dict[str | None, list[Block]]:
    grouped: dict[str | None, list[Block]] = {}
    ordered = sorted(blocks, key=lambda b: (b.source_order, b.source_id.startswith("p")))
    # Image ids sort after the host paragraph that shares source_order:
    # source_id "img..." > "p..." is False for startswith trick.
    # Sort key: order, then paragraphs before images.
    ordered = sorted(
        blocks,
        key=lambda b: (b.source_order, 0 if b.source_id.startswith("p") else 1, b.source_id),
    )
    for block in ordered:
        grouped.setdefault(block.parent_id, []).append(block)
    return grouped


def _clear(blocks: list[Block]) -> None:
    for block in blocks:
        block.final_label = ""


def apply_numbering(blocks: list[Block]) -> None:
    """Walk the tree and assign labels. Does not read teacher numbers."""
    _clear(blocks)
    children = _children(blocks)
    majors = [
        b
        for b in children.get(None, [])
        if b.block_type == "major_question"
    ]
    majors.sort(key=lambda b: (b.source_order, b.source_id))

    for index, major in enumerate(majors, start=1):
        _number_major(major, index, children)

    for block in blocks:
        if block.final_label:
            continue
        if block.block_type == "or_marker":
            block.final_label = "OR"
        else:
            block.final_label = "-"


def _number_major(major: Block, index: int, children: dict[str | None, list[Block]]) -> None:
    kids = children.get(major.source_id, [])
    branch_kids = [c for c in kids if c.block_type == "branch"]
    if major.branch_carrier or branch_kids:
        if major.branch_carrier:
            major.final_label = f"Q{index}) A)"
            next_branch = 2
        else:
            major.final_label = f"Q{index})"
            next_branch = 1
    else:
        major.final_label = f"Q{index})"
        next_branch = 1

    sub_n = 0
    for child in kids:
        if child.block_type == "branch":
            child.final_label = f"{_branch_letter(next_branch)})"
            next_branch += 1
            _number_group(children.get(child.source_id, []), children)
        else:
            sub_n = _number_node(child, children, sub_n)


def _number_group(nodes: list[Block], children: dict[str | None, list[Block]]) -> None:
    sub_n = 0
    for node in nodes:
        sub_n = _number_node(node, children, sub_n)


def _number_node(node: Block, children: dict[str | None, list[Block]], sub_n: int) -> int:
    if node.block_type == "subquestion":
        sub_n += 1
        node.final_label = f"{_roman(sub_n)})"
        choice_n = 0
        for child in children.get(node.source_id, []):
            choice_n = _number_under_sub(child, children, choice_n)
        return sub_n
    if node.block_type == "choice":
        # A choice whose parent is the major (topic options), not a subquestion.
        return sub_n
    if node.block_type == "or_marker":
        node.final_label = "OR"
        return sub_n
    node.final_label = "-"
    for child in children.get(node.source_id, []):
        sub_n = _number_node(child, children, sub_n)
    return sub_n


def _number_under_sub(node: Block, children: dict[str | None, list[Block]], choice_n: int) -> int:
    if node.block_type == "choice":
        labels = []
        slots = max(1, node.slots)
        for _ in range(slots):
            choice_n += 1
            labels.append(f"{_letter(choice_n)})")
        node.final_label = " ".join(labels)
        return choice_n
    if node.block_type == "continuation":
        node.final_label = "-"
        return choice_n
    node.final_label = "-"
    return choice_n


def number_direct_choices(blocks: list[Block]) -> None:
    """Choices parented directly to a major or branch, after the tree walk.

    apply_numbering handles subquestion choices. Topic-option choices that
    hang on the major itself are numbered here, still by tree position only.
    """
    children = _children(blocks)
    for block in blocks:
        if block.block_type not in {"major_question", "branch"}:
            continue
        choice_n = 0
        # Only restart for choices that are direct children, not under subquestions.
        # Subquestion choice counters are already final.
        direct = children.get(block.source_id, [])
        has_sub = any(c.block_type == "subquestion" for c in direct)
        if has_sub:
            continue
        for child in direct:
            if child.block_type != "choice":
                continue
            labels = []
            for _ in range(max(1, child.slots)):
                choice_n += 1
                labels.append(f"{_letter(choice_n)})")
            child.final_label = " ".join(labels)


def assign_labels(blocks: list[Block]) -> dict[str, str]:
    apply_numbering(blocks)
    number_direct_choices(blocks)
    return {b.source_id: b.final_label for b in blocks}


def labels_are_stable(blocks: list[Block]) -> bool:
    first = assign_labels(blocks)
    for block in blocks:
        block.final_label = ""
    second = assign_labels(blocks)
    return first == second
