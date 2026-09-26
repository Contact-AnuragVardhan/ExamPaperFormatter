"""Extract ordered document blocks from a DOCX. Does not classify them."""

from __future__ import annotations

import zipfile
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

from core.models import Block

A_BLIP = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
R_EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"


def _list_level(paragraph) -> int | None:
    pPr = paragraph._p.pPr
    if pPr is None or pPr.numPr is None:
        return None
    ilvl = pPr.numPr.ilvl
    if ilvl is None or ilvl.val is None:
        return 0
    return int(ilvl.val)


def _image_targets(paragraph, rels: dict[str, str]) -> list[str]:
    targets = []
    for blip in paragraph._p.iter(A_BLIP):
        rel_id = blip.get(R_EMBED)
        if rel_id and rel_id in rels:
            targets.append(rels[rel_id])
    return targets


def _image_rels(path: Path) -> dict[str, str]:
    rels: dict[str, str] = {}
    with zipfile.ZipFile(path) as zf:
        name = "word/_rels/document.xml.rels"
        if name not in zf.namelist():
            return rels
        from lxml import etree

        root = etree.fromstring(zf.read(name))
        ns = {"pr": "http://schemas.openxmlformats.org/package/2006/relationships"}
        for rel in root.findall("pr:Relationship", ns):
            if rel.get("Type", "").endswith("/image"):
                rels[rel.get("Id")] = rel.get("Target")
    return rels


def extract_document(path: str | Path) -> list[Block]:
    """Return body paragraphs in order, plus one block per real embedded image.

    Horizontal rules and other non-image drawings are not image blocks.
    Paragraph text is kept exactly as python-docx reads it.
    """
    path = Path(path)
    rels = _image_rels(path)
    document = Document(str(path))
    blocks: list[Block] = []
    image_n = 0
    for index, paragraph in enumerate(document.paragraphs, start=1):
        source_id = f"p{index:04d}"
        blocks.append(
            Block(
                source_id=source_id,
                source_order=index,
                original_text=paragraph.text,
                list_level=_list_level(paragraph),
            )
        )
        for target in _image_targets(paragraph, rels):
            image_n += 1
            blocks.append(
                Block(
                    source_id=f"img{image_n:04d}",
                    source_order=index,
                    original_text="",
                    media_part=target,
                    host_paragraph_id=source_id,
                )
            )
    return blocks
