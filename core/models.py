"""Canonical block model. Hierarchy is stored here before numbering."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


BLOCK_TYPES = (
    "metadata",
    "syllabus",
    "section",
    "major_question",
    "branch",
    "passage",
    "subquestion",
    "choice",
    "alternative",
    "or_marker",
    "continuation",
    "image",
    "other",
)


@dataclass
class Block:
    source_id: str
    source_order: int
    original_text: str
    block_type: str = ""
    parent_id: str | None = None
    confidence: float = 0.0
    reason: str = ""
    final_label: str = ""
    review_required: bool = False
    # Evidence captured at extraction. Discovery may read it; numbering does not.
    list_level: int | None = None
    media_part: str | None = None
    host_paragraph_id: str | None = None
    # How many canonical choice labels this one source block carries.
    slots: int = 1
    # The major stem itself is the first branch (for example "9.(a)").
    branch_carrier: bool = False

    def to_dict(self) -> dict:
        data = asdict(self)
        return data


def clone_blocks(blocks: list[Block]) -> list[Block]:
    return [Block(**asdict(b)) for b in blocks]
