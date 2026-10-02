"""Deterministic labels from an already discovered hierarchy.

Label text comes from a numbering policy. This module only walks the tree.
"""

from __future__ import annotations

from core.models import Block
from reference_profile.numbering import NumberingPolicy


def assign_major(index: int, policy: NumberingPolicy) -> str:
    label = policy.label("major", index)
    if not label:
        raise ValueError("The Reference Profile has no major numbering pattern.")
    return label


def assign_branch(index: int, policy: NumberingPolicy) -> str | None:
    return policy.label("branch", index)


def assign_subquestion(index: int, policy: NumberingPolicy) -> str | None:
    return policy.label("subquestion", index)


def assign_choice(index: int, policy: NumberingPolicy) -> str | None:
    return policy.label("choice", index)


def _children(blocks: list[Block]) -> dict[str | None, list[Block]]:
    grouped: dict[str | None, list[Block]] = {}
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


def apply_numbering(blocks: list[Block], policy: NumberingPolicy) -> None:
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
        _number_major(major, index, children, policy)

    for block in blocks:
        if block.final_label:
            continue
        if block.block_type == "or_marker":
            block.final_label = policy.alternative or "-"
        else:
            block.final_label = "-"


def _number_major(major: Block, index: int, children: dict[str | None, list[Block]], policy: NumberingPolicy) -> None:
    kids = children.get(major.source_id, [])
    major_label = assign_major(index, policy)
    if major.branch_carrier and policy.carrier_splits_branch and policy.branch:
        first = assign_branch(1, policy)
        major.final_label = f"{major_label} {first}" if first else major_label
        next_branch = 2
    else:
        major.final_label = major_label
        next_branch = 1

    sub_n = 0
    for child in kids:
        if child.block_type == "branch":
            label = assign_branch(next_branch, policy) if policy.branch else None
            child.final_label = label or "-"
            if policy.branch:
                next_branch += 1
            _number_group(children.get(child.source_id, []), children, policy)
        else:
            sub_n = _number_node(child, children, sub_n, policy)


def _number_group(nodes: list[Block], children: dict[str | None, list[Block]], policy: NumberingPolicy) -> None:
    sub_n = 0
    for node in nodes:
        sub_n = _number_node(node, children, sub_n, policy)


def _number_node(node: Block, children: dict[str | None, list[Block]], sub_n: int, policy: NumberingPolicy) -> int:
    if node.block_type == "subquestion":
        sub_n += 1
        node.final_label = assign_subquestion(sub_n, policy) or "-"
        choice_n = 0
        for child in children.get(node.source_id, []):
            choice_n = _number_under_sub(child, children, choice_n, policy)
        return sub_n
    if node.block_type == "choice":
        return sub_n
    if node.block_type == "or_marker":
        node.final_label = policy.alternative or "-"
        return sub_n
    node.final_label = "-"
    for child in children.get(node.source_id, []):
        sub_n = _number_node(child, children, sub_n, policy)
    return sub_n


def _number_under_sub(node: Block, children: dict[str | None, list[Block]], choice_n: int, policy: NumberingPolicy) -> int:
    if node.block_type == "choice":
        if not policy.choice:
            node.final_label = "-"
            return choice_n
        labels = []
        slots = max(1, node.slots)
        for _ in range(slots):
            choice_n += 1
            label = assign_choice(choice_n, policy)
            if label:
                labels.append(label)
        node.final_label = " ".join(labels) if labels else "-"
        return choice_n
    if node.block_type == "continuation":
        node.final_label = "-"
        return choice_n
    node.final_label = "-"
    return choice_n


def number_direct_choices(blocks: list[Block], policy: NumberingPolicy) -> None:
    """Choices parented directly to a major or branch, after the tree walk.

    apply_numbering handles subquestion choices. Topic-option choices that
    hang on the major itself are numbered here, still by tree position only.
    """
    if not policy.choice:
        return
    children = _children(blocks)
    for block in blocks:
        if block.block_type not in {"major_question", "branch"}:
            continue
        choice_n = 0
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
                label = assign_choice(choice_n, policy)
                if label:
                    labels.append(label)
            child.final_label = " ".join(labels) if labels else "-"


def assign_labels(blocks: list[Block], policy: NumberingPolicy) -> dict[str, str]:
    apply_numbering(blocks, policy)
    number_direct_choices(blocks, policy)
    return {b.source_id: b.final_label for b in blocks}


def labels_are_stable(blocks: list[Block], policy: NumberingPolicy) -> bool:
    first = assign_labels(blocks, policy)
    for block in blocks:
        block.final_label = ""
    second = assign_labels(blocks, policy)
    return first == second
