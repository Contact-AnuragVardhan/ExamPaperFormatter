"""Inspect one Reference DOCX and build a versioned Reference Profile.

Measurements come from the file. A field that cannot be read reliably is
stored as {"value": null, "status": "not_determined"}. A property the file
simply does not set is {"value": null, "status": "not_set"}.

Nothing in this module is consulted by the teacher-exam formatter.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections import Counter
from pathlib import Path

from docx import Document
from docx.enum.text import WD_UNDERLINE
from docx.oxml.ns import qn
from lxml import etree

PROFILE_VERSION = 1

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A_BLIP = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
WP_EXTENT = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}extent"
R_EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"

_Q_MARKER = re.compile(
    r"^(?P<prefix>[Qq])\s*(?P<number>\d+)\s*(?:\(\s*(?P<letter>[A-Za-z])\s*\))?\s*(?P<punct>[.)])"
)
_PLAIN_MARKER = re.compile(r"^(?P<number>\d+)\s*(?P<punct>[.)])")
_PAREN = re.compile(r"^\(\s*(?P<token>[A-Za-z]+)\s*\)")
_LETTER_PAREN = re.compile(r"^(?P<letter>[A-Za-z])\s*\)")
_LETTER_DOT = re.compile(r"^(?P<letter>[A-Za-z])\s*\.")
_ROMAN = re.compile(
    r"(?i)^M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})$"
)
_ROLE_PATTERNS = (
    ("class_grade", re.compile(r"^(?:class|grade)\b\s*[:\-–—]\s*(.+)$", re.I)),
    ("subject", re.compile(r"^subject\b\s*[:\-–—]\s*(.+)$", re.I)),
    ("time", re.compile(r"^time\b\s*[:\-–—]\s*(.+)$", re.I)),
    (
        "marks",
        re.compile(
            r"^(?:maximum\s+marks|max\.?\s*marks|full\s+marks|m\s*\.\s*m\s*\.?)\b\s*[:\-–—]\s*(.+)$",
            re.I,
        ),
    ),
)
# How a teacher line is recognized as the same role. These accept the
# reference form and the forms already accepted for a teacher exam.
# They do not store a particular grade, subject, or mark value.
_ROLE_RECOGNITION = {
    "class_grade": r"^(?:class|grade)\s*[:\-–—]?\s*(?:\d{1,2}|xii|xi|x)\s*$",
    "subject": r"^subject\b",
    "time": r"^time\b",
    "marks": r"(?:maximum\s+marks|\bm\s*\.\s*m\s*\.)",
}
_ROLE_LABELS = {
    "class_grade": "Class",
    "subject": "Subject",
    "time": "Time",
    "marks": "Maximum marks",
}


def build_profile(reference: str | Path) -> dict:
    """Return a profile describing the reference document."""
    path = Path(reference)
    raw = path.read_bytes()
    doc = Document(str(path))
    paragraphs = [_paragraph(paragraph, index) for index, paragraph in enumerate(doc.paragraphs, start=1)]
    _attach_images(path, paragraphs)
    defaults = _document_defaults(path)
    page, margins = _page_setup(doc)
    sections = _section_series(paragraphs)
    front = _front_matter(paragraphs, sections)
    header = _header_lines(paragraphs, sections, front)
    syllabus = _syllabus(paragraphs, sections, front)
    majors = _major_groups(paragraphs, sections)
    child_kinds = _child_kinds(paragraphs, majors)
    _fill_child_counts(paragraphs, majors, child_kinds)
    images = _images(paragraphs, majors)
    return {
        "profile_version": PROFILE_VERSION,
        "source_reference": path.name,
        "source_hash": {"algorithm": "sha256", "hex": hashlib.sha256(raw).hexdigest()},
        "identity": _identity(header),
        "header": header,
        "structure": {
            "sections": _sections_field(sections),
            "major_questions": _majors_field(majors),
            "hierarchy": _hierarchy_field(majors),
            "syllabus": syllabus,
            "front_matter_headings": [
                {"text": item["text"], "paragraph_index": item["paragraph_index"]} for item in front
            ],
        },
        "numbering": _numbering(majors, child_kinds, paragraphs),
        "images": images,
        "appearance": _appearance(paragraphs, sections, majors, child_kinds, page, margins, defaults),
    }


def write_profile(reference: str | Path, output_dir: str | Path) -> Path:
    """Write the profile JSON and return its path. The source DOCX is not modified."""
    path = Path(reference)
    profile = build_profile(path)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9]+", "-", path.stem).strip("-").lower() or "reference"
    target = directory / f"{stem}-{profile['source_hash']['hex'][:12]}.json"
    target.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def _unknown():
    return {"value": None, "status": "not_determined"}


def _absent():
    return {"value": None, "status": "not_set"}


def _seen(value, **extra):
    data = {"value": value, "status": "observed"}
    data.update(extra)
    return data


def _whole(value):
    rounded = round(float(value), 4)
    if rounded == int(rounded):
        return int(rounded)
    return rounded


def _length_pt(length):
    if length is None:
        return None
    return _whole(length.pt)


def _length_in(length):
    if length is None:
        return None
    return _whole(length.inches)


def _twips(length):
    if length is None:
        return None
    return int(round(length.twips))


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _is_roman(token: str) -> bool:
    # List markers use i/v/x. A single c, d, l, or m is a letter, not a list numeral.
    if not token or not re.fullmatch(r"[ivxIVX]+", token):
        return False
    return bool(_ROMAN.fullmatch(token))


def _underline_value(run):
    value = run.underline
    if value is None:
        return None
    if value is False or value == WD_UNDERLINE.NONE:
        return False
    return True


def _collapse(values):
    present = [value for value in values if value is not None]
    if not present:
        return None, "not_set"
    common, count = Counter(present).most_common(1)[0]
    if count != len(values):
        return common, "mixed"
    return common, "uniform"


def _field_from_values(values):
    if not values:
        return _unknown()
    if all(value is None for value in values):
        return _absent()
    common, kind = _collapse(values)
    if kind == "not_set":
        return _absent()
    return _seen(common, uniform=kind == "uniform")


def _paragraph(paragraph, index: int) -> dict:
    text = paragraph.text or ""
    runs = [run for run in paragraph.runs if run.text]
    fonts = [run.font.name for run in runs]
    sizes = [_length_pt(run.font.size) for run in runs]
    bolds = [None if run.bold is None else bool(run.bold) for run in runs]
    underlines = [_underline_value(run) for run in runs]
    alignment = None if paragraph.alignment is None else paragraph.alignment.name
    return {
        "index": index,
        "text": text,
        "norm": _norm(text),
        "fonts": fonts,
        "sizes": sizes,
        "bolds": bolds,
        "underlines": underlines,
        "alignment": alignment,
        "space_before_pt": _length_pt(paragraph.paragraph_format.space_before),
        "space_after_pt": _length_pt(paragraph.paragraph_format.space_after),
        "left_indent_in": _length_in(paragraph.paragraph_format.left_indent),
        "images": [],
        "marker": _marker(_norm(text)),
    }


def _marker(text: str):
    if not text:
        return None
    match = _Q_MARKER.match(text)
    if match:
        return {
            "kind": "q_number",
            "number": int(match.group("number")),
            "letter": (match.group("letter") or None),
            "text": match.group(0),
            "prefix": match.group("prefix"),
            "punct": match.group("punct"),
        }
    match = _PLAIN_MARKER.match(text)
    if match:
        return {
            "kind": "plain_number",
            "number": int(match.group("number")),
            "letter": None,
            "text": match.group(0),
            "prefix": "",
            "punct": match.group("punct"),
        }
    match = _PAREN.match(text)
    if match and _is_roman(match.group("token")):
        return {"kind": "paren_roman", "text": match.group(0), "number": None, "letter": None}
    if match:
        return {"kind": "paren_letter", "text": match.group(0), "number": None, "letter": match.group("token")}
    match = _LETTER_PAREN.match(text)
    if match:
        return {"kind": "letter_paren", "text": match.group(0), "number": None, "letter": match.group("letter")}
    match = _LETTER_DOT.match(text)
    if match and not _is_roman(match.group("letter")):
        return {"kind": "letter_dot", "text": match.group(0), "number": None, "letter": match.group("letter")}
    return None


def _image_rels(path: Path) -> dict[str, str]:
    rels = {}
    with zipfile.ZipFile(path) as archive:
        name = "word/_rels/document.xml.rels"
        if name not in archive.namelist():
            return rels
        root = etree.fromstring(archive.read(name))
        ns = {"pr": "http://schemas.openxmlformats.org/package/2006/relationships"}
        for rel in root.findall("pr:Relationship", ns):
            if rel.get("Type", "").endswith("/image"):
                rels[rel.get("Id")] = rel.get("Target")
    return rels


def _attach_images(path: Path, paragraphs: list[dict]) -> None:
    rels = _image_rels(path)
    doc = Document(str(path))
    for paragraph, record in zip(doc.paragraphs, paragraphs):
        for blip in paragraph._p.iter(A_BLIP):
            rel_id = blip.get(R_EMBED)
            extent = next(paragraph._p.iter(WP_EXTENT), None)
            width = int(extent.get("cx")) if extent is not None and extent.get("cx") else None
            height = int(extent.get("cy")) if extent is not None and extent.get("cy") else None
            record["images"].append(
                {
                    "media": rels.get(rel_id),
                    "width_emu": width,
                    "height_emu": height,
                }
            )


def _document_defaults(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        if "word/styles.xml" not in archive.namelist():
            return {"font_name": _unknown(), "font_size_pt": _unknown(), "space_after_pt": _unknown(), "line_spacing": _unknown()}
        root = etree.fromstring(archive.read("word/styles.xml"))
    ns = {"w": W_NS}
    rpr = root.find("w:docDefaults/w:rPrDefault/w:rPr", ns)
    ppr = root.find("w:docDefaults/w:pPrDefault/w:pPr", ns)
    font_name = _unknown()
    font_size = _unknown()
    if rpr is not None:
        fonts = rpr.find("w:rFonts", ns)
        if fonts is not None:
            explicit = fonts.get(qn("w:ascii"))
            theme = fonts.get(qn("w:asciiTheme"))
            if explicit:
                font_name = _seen(explicit)
            elif theme:
                font_name = {"value": None, "status": "not_determined", "theme": theme}
        size = rpr.find("w:sz", ns)
        if size is not None and size.get(qn("w:val")):
            font_size = _seen(_whole(int(size.get(qn("w:val"))) / 2))
    space_after = _unknown()
    line_spacing = _unknown()
    if ppr is not None:
        spacing = ppr.find("w:spacing", ns)
        if spacing is not None:
            after = spacing.get(qn("w:after"))
            if after is not None:
                space_after = _seen(_whole(int(after) / 20))
            line = spacing.get(qn("w:line"))
            rule = spacing.get(qn("w:lineRule"))
            if line is not None:
                raw = int(line)
                payload = {"value": raw, "rule": rule, "status": "observed"}
                if rule in (None, "auto"):
                    payload["multiple"] = _whole(raw / 240)
                line_spacing = payload
    return {
        "font_name": font_name,
        "font_size_pt": font_size,
        "space_after_pt": space_after,
        "line_spacing": line_spacing,
    }


def _page_setup(doc: Document):
    if not doc.sections:
        return _unknown(), _unknown()
    section = doc.sections[0]
    orientation = section.orientation
    orientation_name = getattr(orientation, "name", None) if orientation is not None else None
    width_twip = _twips(section.page_width)
    height_twip = _twips(section.page_height)
    top_twip = _twips(section.top_margin)
    right_twip = _twips(section.right_margin)
    bottom_twip = _twips(section.bottom_margin)
    left_twip = _twips(section.left_margin)
    page = {
        "status": "observed",
        "width_twip": width_twip,
        "height_twip": height_twip,
        "width_in": None if width_twip is None else _whole(width_twip / 1440),
        "height_in": None if height_twip is None else _whole(height_twip / 1440),
        "orientation": orientation_name if orientation_name else _unknown(),
    }
    margins = {
        "status": "observed",
        "top_twip": top_twip,
        "right_twip": right_twip,
        "bottom_twip": bottom_twip,
        "left_twip": left_twip,
        "top_in": None if top_twip is None else _whole(top_twip / 1440),
        "right_in": None if right_twip is None else _whole(right_twip / 1440),
        "bottom_in": None if bottom_twip is None else _whole(bottom_twip / 1440),
        "left_in": None if left_twip is None else _whole(left_twip / 1440),
    }
    return page, margins


def _predominant_size(paragraphs: list[dict]):
    sizes = [size for paragraph in paragraphs for size in paragraph["sizes"] if size is not None]
    if not sizes:
        return None
    return Counter(sizes).most_common(1)[0][0]


def _emphasized(paragraph: dict, body_size) -> bool:
    bold, _kind = _collapse(paragraph["bolds"])
    size, _size_kind = _collapse(paragraph["sizes"])
    if bold is True:
        return True
    return size is not None and body_size is not None and size > body_size


def _section_series(paragraphs: list[dict]) -> list[dict]:
    body_size = _predominant_size(paragraphs)
    groups: dict[str, list[dict]] = {}
    for paragraph in paragraphs:
        tokens = paragraph["norm"].split()
        if len(tokens) != 2 or not _emphasized(paragraph, body_size):
            continue
        label = tokens[1]
        if len(label) > 20 or label[-1] in ".?!,:;":
            continue
        groups.setdefault(tokens[0].casefold(), []).append(
            {
                "kind": tokens[0],
                "label": label,
                "heading": paragraph["norm"],
                "paragraph_index": paragraph["index"],
                "paragraph": paragraph,
            }
        )
    series = [items for items in groups.values() if len(items) >= 2]
    if not series:
        return []

    def rank(items: list[dict]):
        sizes = []
        for item in items:
            size, _kind = _collapse(item["paragraph"]["sizes"])
            if size is not None:
                sizes.append(size)
        average = sum(sizes) / len(sizes) if sizes else 0
        return (len(items), average, -items[0]["paragraph_index"])

    chosen = max(series, key=rank)
    cleaned = []
    for order, item in enumerate(chosen, start=1):
        cleaned.append(
            {
                "order": order,
                "kind": item["kind"],
                "label": item["label"],
                "heading": item["heading"],
                "paragraph_index": item["paragraph_index"],
            }
        )
    return cleaned


def _single_token_heading(paragraph: dict, body_size) -> bool:
    return bool(paragraph["norm"]) and len(paragraph["norm"].split()) == 1 and _emphasized(paragraph, body_size)


def _front_matter(paragraphs: list[dict], sections: list[dict]) -> list[dict]:
    body_size = _predominant_size(paragraphs)
    first_section = sections[0]["paragraph_index"] if sections else None
    found = []
    seen_text = False
    for paragraph in paragraphs:
        if first_section is not None and paragraph["index"] >= first_section:
            break
        if paragraph["norm"]:
            seen_text = True
        if seen_text and _single_token_heading(paragraph, body_size):
            found.append({"text": paragraph["norm"], "paragraph_index": paragraph["index"]})
    return found


def _header_lines(paragraphs: list[dict], sections: list[dict], front: list[dict]) -> dict:
    stops = []
    if sections:
        stops.append(sections[0]["paragraph_index"])
    if front:
        stops.append(front[0]["paragraph_index"])
    stop = min(stops) if stops else None
    lines = []
    for paragraph in paragraphs:
        if stop is not None and paragraph["index"] >= stop:
            break
        if not paragraph["norm"]:
            continue
        lines.append(
            {
                "order": len(lines) + 1,
                "paragraph_index": paragraph["index"],
                "text": paragraph["norm"],
                "role": _line_role(paragraph["norm"]),
                "formatting": _formatting(paragraph),
            }
        )
    if not lines:
        return {"status": "not_determined", "ordering": None, "lines": []}
    return {"status": "observed", "ordering": "document_order", "lines": lines}


def _line_role(text: str):
    for name, pattern in _ROLE_PATTERNS:
        match = pattern.match(text)
        if match:
            return _seen(
                name,
                captured=match.group(1).strip(),
                label=_ROLE_LABELS[name],
                pattern=_ROLE_RECOGNITION[name],
            )
    return _unknown()


def _formatting(paragraph: dict) -> dict:
    font, font_kind = _collapse(paragraph["fonts"])
    size, size_kind = _collapse(paragraph["sizes"])
    bold, bold_kind = _collapse(paragraph["bolds"])
    underline, underline_kind = _collapse(paragraph["underlines"])

    def pack(value, kind):
        if kind == "not_set" or value is None and kind != "uniform":
            if kind == "not_set":
                return _absent()
            return _unknown()
        if value is None:
            return _absent()
        return _seen(value, uniform=kind == "uniform")

    return {
        "font_name": pack(font, font_kind),
        "font_size_pt": pack(size, size_kind),
        "bold": pack(bold, bold_kind),
        "underline": pack(underline, underline_kind),
        "alignment": _absent() if paragraph["alignment"] is None else _seen(paragraph["alignment"]),
        "left_indent_in": _absent() if paragraph["left_indent_in"] is None else _seen(paragraph["left_indent_in"]),
        "space_before_pt": _absent() if paragraph["space_before_pt"] is None else _seen(paragraph["space_before_pt"]),
        "space_after_pt": _absent() if paragraph["space_after_pt"] is None else _seen(paragraph["space_after_pt"]),
    }


def _identity(header: dict) -> dict:
    found = {}
    for line in header.get("lines", []):
        role = line["role"]
        if role.get("status") != "observed":
            continue
        found.setdefault(role["value"], {"value": role.get("captured"), "status": "observed", "evidence": line["text"]})
    return {
        "subject": found.get("subject", _unknown()),
        "class_grade": found.get("class_grade", _unknown()),
    }


def _syllabus(paragraphs: list[dict], sections: list[dict], front: list[dict]):
    # The first emphasized one-word heading before the exam body. The word
    # itself is copied from the reference; it is not a fixed title.
    heading = front[0] if front else None
    if heading is None:
        return _unknown()
    boundaries = [item["paragraph_index"] for item in front if item["paragraph_index"] > heading["paragraph_index"]]
    if sections:
        boundaries.append(sections[0]["paragraph_index"])
    stop = min(boundaries) if boundaries else None
    item_count = 0
    for paragraph in paragraphs:
        if paragraph["index"] <= heading["paragraph_index"]:
            continue
        if stop is not None and paragraph["index"] >= stop:
            break
        if paragraph["norm"]:
            item_count += 1
    location = "before_first_section"
    if sections and heading["paragraph_index"] > sections[0]["paragraph_index"]:
        location = "after_first_section"
    return {
        "status": "observed",
        "heading": heading["text"],
        "paragraph_index": heading["paragraph_index"],
        "location": location,
        "item_count": item_count,
    }


def _sections_field(sections: list[dict]):
    if not sections:
        return {"status": "not_determined", "items": []}
    return {
        "status": "observed",
        "items": [
            {
                "order": item["order"],
                "kind": item["kind"],
                "label": item["label"],
                "heading": item["heading"],
                "paragraph_index": item["paragraph_index"],
            }
            for item in sections
        ],
    }


def _major_groups(paragraphs: list[dict], sections: list[dict]) -> list[dict] | None:
    first_section = sections[0]["paragraph_index"] if sections else 0
    by_kind: dict[str, list[dict]] = {}
    for paragraph in paragraphs:
        marker = paragraph["marker"]
        if marker is None or marker["number"] is None:
            continue
        if paragraph["index"] <= first_section:
            continue
        by_kind.setdefault(marker["kind"], []).append({"paragraph_index": paragraph["index"], **marker})
    candidates = []
    for kind, markers in by_kind.items():
        grouped = _walk_major_numbers(markers)
        if grouped:
            candidates.append(grouped)
    if not candidates:
        return None
    return max(candidates, key=len)


def _walk_major_numbers(markers: list[dict]) -> list[dict] | None:
    if not markers or markers[0]["number"] != 1:
        return None
    groups = []
    current = None
    expected = 1
    for marker in markers:
        number = marker["number"]
        if number == expected:
            if current is not None:
                groups.append(current)
            current = {"number": number, "stems": [_stem(marker)]}
            expected += 1
            continue
        if current is not None and number == current["number"] and marker.get("letter"):
            current["stems"].append(_stem(marker))
            continue
        return None
    if current is not None:
        groups.append(current)
    numbers = [group["number"] for group in groups]
    if numbers != list(range(1, len(numbers) + 1)):
        return None
    return groups


def _stem(marker: dict) -> dict:
    return {
        "marker": marker["text"],
        "paragraph_index": marker["paragraph_index"],
        "letter": marker.get("letter"),
        "subquestion_count": 0,
        "choice_count": 0,
    }


def _child_kinds(paragraphs: list[dict], majors: list[dict] | None) -> dict[str, str]:
    if not majors:
        return {"subquestion": "", "choice": []}
    stems = [stem for group in majors for stem in group["stems"]]
    stems.sort(key=lambda item: item["paragraph_index"])
    present: dict[str, list[float | None]] = {}
    for paragraph in paragraphs:
        marker = paragraph["marker"]
        if marker is None or marker["kind"] in {"q_number", "plain_number"}:
            continue
        if not any(stem["paragraph_index"] < paragraph["index"] for stem in stems):
            continue
        present.setdefault(marker["kind"], []).append(paragraph["left_indent_in"])
    if not present:
        return {"subquestion": "", "choice": []}
    if "paren_roman" in present:
        subquestion = "paren_roman"
    elif len(present) == 1:
        subquestion = next(iter(present))
    else:
        def indent_key(kind: str):
            values = [value for value in present[kind] if value is not None]
            return sum(values) / len(values) if values else 0
        subquestion = min(present, key=indent_key)
    choices = [kind for kind in present if kind != subquestion]
    return {"subquestion": subquestion, "choice": choices}


def _fill_child_counts(paragraphs: list[dict], majors: list[dict] | None, child_kinds: dict) -> None:
    if not majors:
        return
    stems = [stem for group in majors for stem in group["stems"]]
    stems.sort(key=lambda item: item["paragraph_index"])
    for index, stem in enumerate(stems):
        stop = stems[index + 1]["paragraph_index"] if index + 1 < len(stems) else None
        for paragraph in paragraphs:
            if paragraph["index"] <= stem["paragraph_index"]:
                continue
            if stop is not None and paragraph["index"] >= stop:
                break
            marker = paragraph["marker"]
            if marker is None:
                continue
            if marker["kind"] == child_kinds["subquestion"]:
                stem["subquestion_count"] += 1
            elif marker["kind"] in child_kinds["choice"]:
                stem["choice_count"] += 1


def _majors_field(majors: list[dict] | None):
    if not majors:
        return {"status": "not_determined", "count": None, "numbers": []}
    return {
        "status": "observed",
        "count": len(majors),
        "numbers": [group["number"] for group in majors],
    }


def _hierarchy_field(majors: list[dict] | None):
    if not majors:
        return {"status": "not_determined", "majors": []}
    return {
        "status": "observed",
        "majors": [
            {
                "number": group["number"],
                "branches": [stem["letter"] for stem in group["stems"] if stem["letter"]],
                "stems": [
                    {
                        "marker": stem["marker"],
                        "paragraph_index": stem["paragraph_index"],
                        "subquestion_count": stem["subquestion_count"],
                        "choice_count": stem["choice_count"],
                    }
                    for stem in group["stems"]
                ],
            }
            for group in majors
        ],
    }


def _examples(markers: list[str], limit: int = 4) -> list[str]:
    found = []
    for marker in markers:
        if marker not in found:
            found.append(marker)
        if len(found) == limit:
            break
    return found


def _pattern_shapes(kind: str, markers: list[str]) -> list[str]:
    if not markers:
        return []
    if kind == "q_number":
        prefix = markers[0][0]
        punct = markers[0][-1]
        shapes = []
        if any("(" not in marker for marker in markers):
            shapes.append(f"{prefix}{{n}}{punct}")
        if any("(" in marker for marker in markers):
            shapes.append(f"{prefix}{{n}}({{letter}}){punct}")
        return shapes
    if kind == "plain_number":
        return [f"{{n}}{markers[0][-1]}"]
    if kind == "paren_roman":
        return ["({roman})"]
    if kind == "paren_letter":
        letter = re.search(r"[A-Za-z]", markers[0]).group(0)
        token = "{Letter}" if letter.isupper() else "{letter}"
        return [f"({token})"]
    if kind == "letter_paren":
        token = "{Letter}" if markers[0][0].isupper() else "{letter}"
        return [f"{token})"]
    if kind == "letter_dot":
        token = "{Letter}" if markers[0][0].isupper() else "{letter}"
        return [f"{token}."]
    return []


def _markers_of_kind(paragraphs: list[dict], kind: str) -> list[str]:
    return [paragraph["marker"]["text"] for paragraph in paragraphs if paragraph["marker"] and paragraph["marker"]["kind"] == kind]


def _numbering(majors: list[dict] | None, child_kinds: dict, paragraphs: list[dict]) -> dict:
    major = _unknown()
    branch = _unknown()
    if majors:
        markers = [stem["marker"] for group in majors for stem in group["stems"]]
        kind = "q_number" if markers and markers[0][:1] in {"Q", "q"} else "plain_number"
        major = {
            "status": "observed",
            "patterns": _pattern_shapes(kind, markers),
            "examples": _major_examples(majors),
        }
        letters = [stem["letter"] for group in majors for stem in group["stems"] if stem["letter"]]
        repeated = [group for group in majors if len(group["stems"]) > 1 and any(stem["letter"] for stem in group["stems"])]
        if repeated:
            branch = {
                "status": "observed",
                "patterns": _pattern_shapes(kind, [stem["marker"] for group in repeated for stem in group["stems"]]),
                "letters": letters,
                "examples": _examples([stem["marker"] for group in repeated for stem in group["stems"]]),
            }
    subquestion = _unknown()
    if child_kinds.get("subquestion"):
        kind = child_kinds["subquestion"]
        markers = _markers_of_kind(paragraphs, kind)
        subquestion = {
            "status": "observed",
            "patterns": _pattern_shapes(kind, markers),
            "examples": _examples(markers),
        }
    choice = _unknown()
    choice_kinds = child_kinds.get("choice") or []
    if choice_kinds:
        patterns = []
        for kind in choice_kinds:
            markers = _markers_of_kind(paragraphs, kind)
            patterns.append(
                {
                    "patterns": _pattern_shapes(kind, markers),
                    "examples": _examples(markers),
                    "count": len(markers),
                }
            )
        choice = {"status": "observed", "kinds": patterns}
    return {
        "major": major,
        "subquestion": subquestion,
        "choice": choice,
        "branch": branch,
        "alternative": _alternative(paragraphs, majors),
    }


def _major_examples(majors: list[dict]) -> list[str]:
    markers = [stem["marker"] for group in majors for stem in group["stems"]]
    chosen = []
    if markers:
        chosen.append(markers[0])
    branched = next((marker for marker in markers if "(" in marker), None)
    if branched and branched not in chosen:
        chosen.append(branched)
    if markers and markers[-1] not in chosen:
        chosen.append(markers[-1])
    return chosen


def _alternative(paragraphs: list[dict], majors: list[dict] | None):
    if not majors:
        return _unknown()
    body_size = _predominant_size(paragraphs)
    stems = [stem["paragraph_index"] for group in majors for stem in group["stems"]]
    found = []
    for paragraph in paragraphs:
        if not re.fullmatch(r"[A-Za-z]{2,15}", paragraph["norm"]):
            continue
        if not any(left < paragraph["index"] < right for left, right in zip(stems, stems[1:])):
            continue
        size, _kind = _collapse(paragraph["sizes"])
        bold, _bold_kind = _collapse(paragraph["bolds"])
        if bold is True and size is not None and body_size is not None and size > body_size:
            continue
        found.append(paragraph["norm"])
    if not found:
        return _unknown()
    return _seen(_examples(found), count=len(found))


def _images(paragraphs: list[dict], majors: list[dict] | None) -> dict:
    stems = []
    if majors:
        stems = [stem for group in majors for stem in group["stems"]]
    items = []
    for paragraph in paragraphs:
        for image in paragraph["images"]:
            host = None
            earlier = [stem for stem in stems if stem["paragraph_index"] <= paragraph["index"]]
            if earlier:
                host = earlier[-1]["marker"]
            item = {
                "paragraph_index": paragraph["index"],
                "media": image["media"],
                "host_marker": host if host else None,
            }
            if image["width_emu"] is None or image["height_emu"] is None:
                item["width_emu"] = _unknown()
                item["height_emu"] = _unknown()
            else:
                item["width_emu"] = image["width_emu"]
                item["height_emu"] = image["height_emu"]
                item["width_in"] = _whole(image["width_emu"] / 914400)
                item["height_in"] = _whole(image["height_emu"] / 914400)
            items.append(item)
    return {"status": "observed", "count": len(items), "items": items}


def _role_paragraphs(paragraphs, sections, majors, child_kinds):
    section_indexes = {item["paragraph_index"] for item in sections}
    stem_indexes = set()
    stems = []
    if majors:
        stems = [stem for group in majors for stem in group["stems"]]
        stem_indexes = {stem["paragraph_index"] for stem in stems}
    sub_kind = child_kinds.get("subquestion")
    choice_kinds = set(child_kinds.get("choice") or [])
    roles = {"section_heading": [], "major_question": [], "subquestion": [], "choice": [], "body": []}
    for paragraph in paragraphs:
        if not paragraph["norm"] and not paragraph["images"]:
            continue
        if paragraph["index"] in section_indexes:
            roles["section_heading"].append(paragraph)
        elif paragraph["index"] in stem_indexes:
            roles["major_question"].append(paragraph)
        elif paragraph["marker"] and paragraph["marker"]["kind"] == sub_kind:
            roles["subquestion"].append(paragraph)
        elif paragraph["marker"] and paragraph["marker"]["kind"] in choice_kinds:
            roles["choice"].append(paragraph)
        elif paragraph["norm"] and paragraph["marker"] is None and paragraph["index"] not in section_indexes:
            roles["body"].append(paragraph)
    return roles


def _summarize_role(paragraphs: list[dict]):
    if not paragraphs:
        return _unknown()
    fonts = [font for paragraph in paragraphs for font in paragraph["fonts"]]
    sizes = [size for paragraph in paragraphs for size in paragraph["sizes"]]
    bolds = [bold for paragraph in paragraphs for bold in paragraph["bolds"]]
    underlines = [value for paragraph in paragraphs for value in paragraph["underlines"]]
    alignments = [paragraph["alignment"] for paragraph in paragraphs]
    indents = [paragraph["left_indent_in"] for paragraph in paragraphs]
    before = [paragraph["space_before_pt"] for paragraph in paragraphs]
    after = [paragraph["space_after_pt"] for paragraph in paragraphs]
    return {
        "status": "observed",
        "paragraph_count": len(paragraphs),
        "font_name": _field_from_values(fonts),
        "font_size_pt": _field_from_values(sizes),
        "bold": _field_from_values(bolds),
        "underline": _field_from_values(underlines),
        "alignment": _field_from_values(alignments),
        "left_indent_in": _field_from_values(indents),
        "space_before_pt": _field_from_values(before),
        "space_after_pt": _field_from_values(after),
        "line_spacing": _absent(),
    }


def _predominant_font(paragraphs: list[dict]):
    counts = Counter()
    for paragraph in paragraphs:
        for font, run_text_size in zip(paragraph["fonts"], paragraph["sizes"]):
            if font:
                counts[font] += 1
    if not counts:
        return _unknown()
    font, count = counts.most_common(1)[0]
    return _seen(font, run_count=count)


def _appearance(paragraphs, sections, majors, child_kinds, page, margins, defaults) -> dict:
    roles = _role_paragraphs(paragraphs, sections, majors, child_kinds)
    return {
        "page": page,
        "margins": margins,
        "predominant_font": _predominant_font(paragraphs),
        "document_defaults": defaults,
        "roles": {name: _summarize_role(items) for name, items in roles.items()},
    }
