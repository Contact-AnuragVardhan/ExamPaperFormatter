"""Conservative spelling correction for a Word exam.

This module does not format an exam, does not read a reference, and does not
call the FORMAT instructions. It copies a .docx, replaces only an unmistakable
misspelling inside a single Word text node, and writes a separate text report.
"""

from __future__ import annotations

import html
import gzip
import logging
import os
import re
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZipInfo, BadZipFile
from xml.etree import ElementTree as ET

from runtime_logging import event

log = logging.getLogger("exam.spellcheck")

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MATH = "http://schemas.openxmlformats.org/officeDocument/2006/math"
W_T = f"{{{W}}}t"
W_TAB = f"{{{W}}}tab"
W_BR = f"{{{W}}}br"
W_CR = f"{{{W}}}cr"
W_FLD = f"{{{W}}}fldCharType"

DATA = Path(__file__).resolve().parent / "spellcheck_data"
EN_MIN_FREQUENCY = 1000
EN_FREQUENCY_RATIO = 20
HI_MIN_FREQUENCY = 10
EN_MIN_LENGTH = 4
HI_MIN_LENGTH = 4
MAX_TOKEN = 40

# A word inside any of these ancestors is left alone: links, pictures,
# equations, fields, and revision markup.
_BLOCKED = {"hyperlink", "drawing", "pict", "object", "fldSimple", "del", "ins", "moveFrom", "moveTo"}
_SKIP_BLOCKS = {"sectPr", "tblPr", "tblGrid", "trPr", "tcPr", "pPr", "bookmarkStart", "bookmarkEnd", "proofErr"}

_WT_RE = re.compile(br"<w:t(?P<emptyAttrs>\s[^>]*)?/>|<w:t(?P<attrs>\s[^>]*)?>(?P<text>[^<]*)</w:t>")
# Hindi words are letters plus their combining marks. Danda, digits, and
# abbreviation signs stay outside the word so punctuation is never "corrected".
_TOKEN_RE = re.compile(
    r"[A-Za-z]+|(?:[\u0904-\u0939\u0958-\u0961\u0972-\u097F][\u0900-\u0903\u093A-\u094D\u0951-\u0957\u0962-\u0963]*)+"
)
_URL_RE = re.compile(r"https?://\S+|www\.\S+|\b[\w.+-]+@[\w.-]+\.\w+\b", re.IGNORECASE)
_DEVANAGARI_DIGIT = re.compile(r"[\u0966-\u096F]")
_PROTECTED_XML = (
    re.compile(br"<m:oMathPara\b.*?</m:oMathPara>", re.DOTALL),
    re.compile(br"<m:oMath\b.*?</m:oMath>", re.DOTALL),
    re.compile(br"<w:instrText\b.*?</w:instrText>", re.DOTALL),
    re.compile(br"<w:fldSimple\b.*?</w:fldSimple>", re.DOTALL),
    re.compile(br"<w:drawing\b.*?</w:drawing>", re.DOTALL),
    re.compile(br"<w:pict\b.*?</w:pict>", re.DOTALL),
    re.compile(br"<w:object\b.*?</w:object>", re.DOTALL),
)
_TEXT_PART = re.compile(r"word/(?:document|header\d+|footer\d+)\.xml")

_LEX_LOCK = threading.Lock()
_LEX = None


class SpellcheckError(Exception):
    """The document could not be updated without risking unrelated content."""


@dataclass
class SkippedWord:
    word: str
    location: str
    reason: str


@dataclass
class LineChange:
    location: str
    original: str
    corrected: str
    pairs: list[tuple[str, str]]


@dataclass
class SpellcheckResult:
    docx_path: Path
    report_path: Path
    changed_lines: int
    corrections: int
    skipped: tuple[SkippedWord, ...]
    elapsed_seconds: float
    api_calls: int = 0
    summary: str = ""


def report_path_for(docx_path: Path) -> Path:
    name = Path(docx_path).name
    marker = "_Spellchecked.docx"
    if not name.endswith(marker):
        raise SpellcheckError("The spelling report is unavailable.")
    return Path(docx_path).with_name(name[: -len(marker)] + "_Spelling_Changes.txt")


@dataclass
class _Lexicon:
    en_words: set[str]
    en_freq: dict[str, int]
    hi_known: set[str]
    hi_freq: dict[str, int]
    hi_alphabet: str


@dataclass
class _Piece:
    text: str
    node: ET.Element | None


@dataclass
class _Paragraph:
    location: str
    original: str
    corrected: str
    pairs: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[SkippedWord] = field(default_factory=list)


def run(source: Path, output_dir: Path | None = None, stem: str | None = None) -> SpellcheckResult:
    """Spellcheck one .docx and publish both outputs, or neither of them."""
    started = time.perf_counter()
    source = Path(source)
    if source.suffix.lower() != ".docx" or not source.is_file():
        raise SpellcheckError("Choose a Word .docx exam file.")
    original = source.read_bytes()
    output_dir = Path(output_dir) if output_dir is not None else source.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    piece = _safe_stem(stem or source.stem)
    docx_path = output_dir / f"{piece}_Spellchecked.docx"
    report_path = output_dir / f"{piece}_Spelling_Changes.txt"
    if docx_path.resolve() == source.resolve() or report_path.resolve() == source.resolve():
        raise SpellcheckError("Refusing to overwrite the source exam.")
    try:
        package = _spellcheck_package(original)
    except SpellcheckError:
        if source.read_bytes() != original:
            raise
        raise
    if source.read_bytes() != original:
        raise SpellcheckError("The source exam was modified and no output was published.")
    report = _render_report(package.changes)
    _publish(docx_path, report_path, package.data, report)
    if source.read_bytes() != original:
        _remove_published(docx_path, report_path)
        raise SpellcheckError("The source exam was modified and the new output was removed.")
    elapsed = time.perf_counter() - started
    corrections = sum(len(change.pairs) for change in package.changes)
    summary = (
        "No confident spelling corrections found."
        if corrections == 0
        else f"TESTING SC finished. {len(package.changes)} line(s) changed, {corrections} word correction(s)."
    )
    event(
        log,
        "spellcheck.completed",
        changed_lines=len(package.changes),
        corrections=corrections,
        skipped=len(package.skipped),
        elapsed_ms=round(elapsed * 1000),
        api_calls=0,
        output_bytes=len(package.data),
    )
    return SpellcheckResult(
        docx_path=docx_path,
        report_path=report_path,
        changed_lines=len(package.changes),
        corrections=corrections,
        skipped=tuple(package.skipped),
        elapsed_seconds=elapsed,
        api_calls=0,
        summary=summary,
    )


@dataclass
class _Package:
    data: bytes
    changes: list[LineChange]
    skipped: list[SkippedWord]


def _spellcheck_package(original: bytes) -> _Package:
    try:
        with ZipFile(BytesIO(original)) as package:
            names = package.namelist()
            parts = {name: package.read(name) for name in names}
    except (BadZipFile, RuntimeError, ValueError) as exc:
        raise SpellcheckError("That exam file is not a readable Word .docx.") from exc
    if "word/document.xml" not in parts:
        raise SpellcheckError("That exam file is not a readable Word .docx.")
    lexicon = _lexicon()
    replacements: dict[str, bytes] = {}
    changes: list[LineChange] = []
    skipped: list[SkippedWord] = []
    for name in names:
        if not _TEXT_PART.fullmatch(name):
            continue
        updated, part_changes, part_skipped = _edit_part(parts[name], name, lexicon)
        skipped.extend(part_skipped)
        changes.extend(part_changes)
        if updated is not None:
            replacements[name] = updated
    if not replacements:
        data = original
    else:
        data = _rezip(original, replacements)
        _assert_package_safe(original, data, set(replacements))
    return _Package(data, changes, skipped)


def _edit_part(xml: bytes, part_name: str, lexicon: _Lexicon):
    if b"encoding='utf-16'" in xml[:200].lower() or xml.startswith(b"\xff\xfe"):
        raise SpellcheckError("This Word file uses an unsupported text encoding.")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise SpellcheckError("This Word file is damaged.") from exc
    nodes = list(root.iter(W_T))
    matches = list(_WT_RE.finditer(xml))
    if len(matches) != len(nodes):
        raise SpellcheckError("Word text could not be located safely, so nothing was changed.")
    for node, match in zip(nodes, matches):
        raw = match.group("text")
        plain = "" if raw is None else html.unescape(raw.decode("utf-8"))
        if plain != (node.text or ""):
            raise SpellcheckError("Word text could not be located safely, so nothing was changed.")
    paragraphs = _paragraphs(root, part_name)
    node_edits: dict[int, list[tuple[int, int, str]]] = {}
    node_index = {id(node): index for index, node in enumerate(nodes)}
    original_plain = {id(node): (node.text or "") for node in nodes}
    for paragraph in paragraphs:
        for start, end, new in _paragraph_edits(paragraph):
            piece = paragraph.pieces[paragraph.piece_at[start]]
            node = piece.node
            if node is None:
                continue
            offset = paragraph.offset_at[start]
            node_edits.setdefault(id(node), []).append((offset, offset + (end - start), new))
    raw_replacements: dict[int, bytes] = {}
    for node in nodes:
        edits = node_edits.get(id(node))
        if not edits:
            continue
        match = matches[node_index[id(node)]]
        raw = match.group("text").decode("utf-8")
        plain, spans = _unescape_map(raw)
        if plain != original_plain[id(node)]:
            raise SpellcheckError("Word text could not be located safely, so nothing was changed.")
        updated = raw
        for start, end, new in sorted(edits, reverse=True):
            raw_start = spans[start][0]
            raw_end = spans[end - 1][1]
            if "&" in raw[raw_start:raw_end]:
                raise SpellcheckError("A spelling change overlapped XML markup, so nothing was published.")
            updated = updated[:raw_start] + new + updated[raw_end:]
        if html.unescape(updated) != _apply_edits(original_plain[id(node)], edits):
            raise SpellcheckError("A spelling change could not be verified, so nothing was published.")
        node.text = html.unescape(updated)
        raw_replacements[node_index[id(node)]] = updated.encode("utf-8")
    corrected = {_id: (node.text or "") for _id, node in ((id(node), node) for node in nodes)}
    for paragraph in paragraphs:
        paragraph.corrected = _join_pieces(paragraph, corrected)
        if paragraph.pairs:
            if paragraph.corrected == paragraph.original:
                raise SpellcheckError("A reported correction did not change the paragraph.")
    if not raw_replacements:
        return None, [], [item for paragraph in paragraphs for item in paragraph.skipped]
    rewritten = _splice_text_nodes(xml, matches, raw_replacements)
    _assert_rewritten(rewritten, part_name, paragraphs)
    _assert_protected_slices(xml, rewritten)
    changes = [
        LineChange(paragraph.location, paragraph.original, paragraph.corrected, list(paragraph.pairs))
        for paragraph in paragraphs
        if paragraph.pairs
    ]
    skipped = [item for paragraph in paragraphs for item in paragraph.skipped]
    return rewritten, changes, skipped


def _paragraph_edits(paragraph: _Paragraph):
    """Yield (start, end, replacement) in the paragraph's plain text."""
    edits = []
    occupied = _frozen_spans(paragraph.original)
    for match in _TOKEN_RE.finditer(paragraph.original):
        if any(not (match.end() <= stop or match.start() >= start) for start, stop in occupied):
            continue
        token = match.group()
        if not _single_piece(paragraph, match.start(), match.end()):
            decision = _decision(token)
            if decision[0] == "correct":
                paragraph.skipped.append(SkippedWord(token, paragraph.location, "formatting boundary"))
            continue
        piece = paragraph.pieces[paragraph.piece_at[match.start()]]
        if piece.node is None:
            decision = _decision(token)
            if decision[0] == "correct":
                paragraph.skipped.append(SkippedWord(token, paragraph.location, "protected text"))
            continue
        decision = _decision(token)
        if decision[0] == "correct":
            edits.append((match.start(), match.end(), decision[1]))
            paragraph.pairs.append((token, decision[1]))
        elif decision[0] == "skip":
            paragraph.skipped.append(SkippedWord(token, paragraph.location, decision[1]))
    return edits


def _single_piece(paragraph: _Paragraph, start: int, end: int) -> bool:
    indexes = {paragraph.piece_at[pos] for pos in range(start, end)}
    return len(indexes) == 1


def _decision(token: str) -> tuple[str, str]:
    if "\u0900" <= token[0] <= "\u097f":
        return _hindi_decision(token)
    return _english_decision(token)


def _english_decision(token: str) -> tuple[str, str]:
    if not token.isalpha() or not EN_MIN_LENGTH <= len(token) <= MAX_TOKEN:
        return ("ignore", "")
    # Only a simple capital (Fingerprins) may take a spelling fix. All capitals
    # and internal capitals stay put, which keeps acronyms and names like Fischer.
    if token != token.lower():
        if token[0].isupper() and token[1:].islower():
            kind, chosen = _english_decision(token.lower())
            if kind == "correct" and _plural_pair_choice(token.lower(), chosen):
                return ("correct", chosen[:1].upper() + chosen[1:])
        return ("ignore", "")
    lexicon = _lexicon()
    if token in lexicon.en_words:
        return ("ignore", "")
    # A dictionary entry with no usage count is not a rival spelling.
    ranked = sorted(
        (
            word
            for word in _edits(token, "abcdefghijklmnopqrstuvwxyz")
            if word in lexicon.en_words and lexicon.en_freq.get(word, 0) > 0
        ),
        key=lambda word: lexicon.en_freq.get(word, 0),
        reverse=True,
    )
    if not ranked:
        return ("ignore", "")
    best = ranked[0]
    best_freq = lexicon.en_freq.get(best, 0)
    if _ambiguous_prefix(token, best, lexicon):
        return ("skip", "uncertain")
    # fingerprint/fingerprints is one word. The frequency ratio does not apply.
    if _is_plural_pair(ranked) and best_freq >= EN_MIN_FREQUENCY:
        return ("correct", best)
    if len(ranked) > 1:
        second_freq = lexicon.en_freq.get(ranked[1], 0)
        if second_freq <= 0 or best_freq < EN_FREQUENCY_RATIO * second_freq or best_freq < EN_MIN_FREQUENCY:
            return ("skip", "uncertain")
    elif best_freq < EN_MIN_FREQUENCY:
        return ("skip", "uncertain")
    return ("correct", best)


def _is_plural_pair(words: list[str]) -> bool:
    if len(words) != 2:
        return False
    short, long = sorted(words, key=len)
    return long == short + "s" or long == short + "es"


def _plural_pair_choice(token: str, chosen: str) -> bool:
    lexicon = _lexicon()
    ranked = sorted(
        (
            word
            for word in _edits(token, "abcdefghijklmnopqrstuvwxyz")
            if word in lexicon.en_words and lexicon.en_freq.get(word, 0) > 0
        ),
        key=lambda word: lexicon.en_freq.get(word, 0),
        reverse=True,
    )
    return bool(ranked) and ranked[0] == chosen and _is_plural_pair(ranked)


def _ambiguous_prefix(token: str, best: str, lexicon: _Lexicon) -> bool:
    """A clipped word such as promis matches both promise and promised."""
    if not best.startswith(token) or len(best) == len(token):
        return False
    return any(
        word.startswith(token) and word != best and lexicon.en_freq.get(word, 0) >= EN_MIN_FREQUENCY
        for word in lexicon.en_words
    )


def _hindi_decision(token: str) -> tuple[str, str]:
    if _DEVANAGARI_DIGIT.search(token) or len(token) > MAX_TOKEN:
        return ("ignore", "")
    key = unicodedata.normalize("NFC", token)
    if key != token:
        return ("skip", "uncertain")
    lexicon = _lexicon()
    # A rare dictionary entry can be a clipped word, as निय is for नियम.
    # A short unknown token stays unchanged.
    if len(key) < HI_MIN_LENGTH:
        clipped = _clipped_completion(key, lexicon)
        return ("correct", clipped) if clipped else ("ignore", "")
    if key in lexicon.hi_known:
        if lexicon.hi_freq.get(key, 0) >= HI_MIN_FREQUENCY:
            return ("ignore", "")
        clipped = _clipped_completion(key, lexicon)
        return ("correct", clipped) if clipped else ("ignore", "")
    ranked = [word for word in _edits(key, lexicon.hi_alphabet) if word in lexicon.hi_known]
    if len(ranked) != 1:
        return ("skip", "uncertain") if ranked else ("ignore", "")
    candidate = ranked[0]
    if lexicon.hi_freq.get(candidate, 0) < HI_MIN_FREQUENCY:
        return ("skip", "uncertain")
    return ("correct", candidate)


def _clipped_completion(key: str, lexicon: _Lexicon) -> str | None:
    """One added letter on a rare known word, with no other common neighbor."""
    frequency = lexicon.hi_freq.get(key, 0)
    if len(key) < 3 or frequency <= 0 or frequency >= HI_MIN_FREQUENCY:
        return None
    strong = [
        word
        for word in _edits(key, lexicon.hi_alphabet)
        if word in lexicon.hi_known and lexicon.hi_freq.get(word, 0) >= HI_MIN_FREQUENCY
    ]
    if len(strong) != 1:
        return None
    chosen = strong[0]
    if chosen.startswith(key) and len(chosen) == len(key) + 1:
        return chosen
    return None


def _edits(word: str, alphabet: str) -> set[str]:
    splits = [(word[:index], word[index:]) for index in range(len(word) + 1)]
    found = {left + right[1:] for left, right in splits if right}
    found.update(left + right[1] + right[0] + right[2:] for left, right in splits if len(right) > 1)
    found.update(left + letter + right[1:] for left, right in splits if right for letter in alphabet if letter != right[0])
    found.update(left + letter + right for left, right in splits for letter in alphabet)
    found.discard(word)
    return found


def _paragraphs(root: ET.Element, part_name: str) -> list[_Paragraph]:
    counters = {"table": 0}
    if part_name == "word/document.xml":
        body = root.find(f"{{{W}}}body")
        if body is None:
            raise SpellcheckError("This Word file has no document body.")
        found: list[_Paragraph] = []
        _walk_blocks(body, counters, "body", found)
        return found
    header = re.fullmatch(r"word/header(\d+)\.xml", part_name)
    footer = re.fullmatch(r"word/footer(\d+)\.xml", part_name)
    if header:
        kind = f"Header {header.group(1)}"
    elif footer:
        kind = f"Footer {footer.group(1)}"
    else:
        return []
    found = []
    _walk_blocks(root, counters, kind, found)
    return found


def _walk_blocks(element: ET.Element, counters: dict, kind: str, found: list[_Paragraph]) -> None:
    paragraph_number = 0
    for child in list(element):
        tag = _local(child)
        if tag == "p":
            paragraph_number += 1
            if kind == "body":
                counters["body"] = counters.get("body", 0) + 1
                location = f"Body paragraph {counters['body']}"
            elif kind.startswith("Header") or kind.startswith("Footer"):
                location = f"{kind} paragraph {paragraph_number}"
            else:
                location = f"{kind} / paragraph {paragraph_number}"
            found.append(_inspect_paragraph(child, location))
        elif tag == "tbl":
            counters["table"] += 1
            table_number = counters["table"]
            for row_number, row in enumerate((item for item in child if _local(item) == "tr"), 1):
                for cell_number, cell in enumerate((item for item in row if _local(item) == "tc"), 1):
                    prefix = f"Table {table_number} / row {row_number} / cell {cell_number}"
                    if kind not in {"body"} and not _is_story(kind):
                        prefix = f"{kind} / {prefix}"
                    elif kind.startswith("Header") or kind.startswith("Footer"):
                        prefix = f"{kind} / {prefix}"
                    _walk_blocks(cell, counters, prefix, found)
        elif tag in _SKIP_BLOCKS:
            continue
        else:
            _walk_blocks(child, counters, kind, found)


def _is_story(kind: str) -> bool:
    return kind == "body" or kind.startswith("Header") or kind.startswith("Footer")


def _inspect_paragraph(paragraph: ET.Element, location: str) -> _Paragraph:
    safe = _safe_ids(paragraph)
    pieces = _pieces(paragraph, safe)
    text = "".join(piece.text for piece in pieces)
    piece_at: list[int] = []
    offset_at: list[int] = []
    for index, piece in enumerate(pieces):
        for offset in range(len(piece.text)):
            piece_at.append(index)
            offset_at.append(offset)
    item = _Paragraph(location, text, text)
    item.pieces = pieces
    item.piece_at = piece_at
    item.offset_at = offset_at
    return item


def _safe_ids(paragraph: ET.Element) -> set[int]:
    safe: set[int] = set()
    field_depth = 0

    def walk(element: ET.Element, blocked: bool) -> None:
        nonlocal field_depth
        namespace, tag = _split(element)
        now_blocked = blocked or namespace == MATH or tag in _BLOCKED
        if tag == "fldChar" and namespace == W:
            marker = element.get(W_FLD)
            if marker == "begin":
                field_depth += 1
            elif marker == "end":
                field_depth = max(0, field_depth - 1)
        if tag == "t" and namespace == W and not now_blocked and field_depth == 0:
            safe.add(id(element))
        for child in element:
            walk(child, now_blocked)

    walk(paragraph, False)
    return safe


def _pieces(paragraph: ET.Element, safe: set[int]) -> list[_Piece]:
    pieces: list[_Piece] = []

    def walk(element: ET.Element) -> None:
        if element.tag == W_T:
            pieces.append(_Piece(element.text or "", element if id(element) in safe else None))
            return
        if element.tag in {W_TAB}:
            pieces.append(_Piece("\t", None))
            return
        if element.tag in {W_BR, W_CR}:
            pieces.append(_Piece("\n", None))
            return
        for child in element:
            walk(child)

    walk(paragraph)
    return pieces


def _join_pieces(paragraph: _Paragraph, texts: dict[int, str]) -> str:
    parts = []
    for piece in paragraph.pieces:
        if piece.node is None:
            parts.append(piece.text)
        else:
            parts.append(texts.get(id(piece.node), piece.text))
    return "".join(parts)


def _apply_edits(text: str, edits: list[tuple[int, int, str]]) -> str:
    for start, end, new in sorted(edits, reverse=True):
        text = text[:start] + new + text[end:]
    return text


def _unescape_map(raw: str) -> tuple[str, list[tuple[int, int]]]:
    plain: list[str] = []
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(raw):
        if raw[index] == "&":
            end = raw.find(";", index)
            token = raw[index:] if end < 0 else raw[index : end + 1]
            decoded = html.unescape(token if end >= 0 else raw[index])
            if end < 0 or decoded == token:
                plain.append(raw[index])
                spans.append((index, index + 1))
                index += 1
                continue
            if len(decoded) != 1:
                raise SpellcheckError("A text node used unsupported XML characters.")
            plain.append(decoded)
            spans.append((index, end + 1))
            index = end + 1
        else:
            plain.append(raw[index])
            spans.append((index, index + 1))
            index += 1
    return "".join(plain), spans


def _splice_text_nodes(xml: bytes, matches: list[re.Match[bytes]], replacements: dict[int, bytes]) -> bytes:
    updated = xml
    for index in sorted(replacements, reverse=True):
        match = matches[index]
        if match.group("text") is None:
            raise SpellcheckError("An empty Word text node could not be edited safely.")
        start, end = match.start("text"), match.end("text")
        updated = updated[:start] + replacements[index] + updated[end:]
    return updated


def _assert_rewritten(xml: bytes, part_name: str, expected: list[_Paragraph]) -> None:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise SpellcheckError("The spellchecked Word file could not be read back.") from exc
    actual = _paragraphs(root, part_name)
    if [item.original for item in actual] != [item.corrected for item in expected]:
        raise SpellcheckError("The saved paragraphs did not match the spelling report, so nothing was published.")
    for item in expected:
        for old, new in item.pairs:
            saved = next(paragraph.original for paragraph in actual if paragraph.location == item.location)
            if old == new or new not in saved or saved != item.corrected:
                raise SpellcheckError("A correction was not present in the saved document.")


def _assert_protected_slices(before: bytes, after: bytes) -> None:
    for pattern in _PROTECTED_XML:
        if pattern.findall(before) != pattern.findall(after):
            raise SpellcheckError("Protected Word content would have changed, so nothing was published.")


def _assert_package_safe(original: bytes, updated: bytes, changed_parts: set[str]) -> None:
    try:
        with ZipFile(BytesIO(original)) as before, ZipFile(BytesIO(updated)) as after:
            if before.namelist() != after.namelist():
                raise SpellcheckError("The Word package changed shape, so nothing was published.")
            for name in before.namelist():
                old = before.read(name)
                new = after.read(name)
                if name not in changed_parts and old != new:
                    raise SpellcheckError("A protected Word part would have changed, so nothing was published.")
                if name in changed_parts:
                    _assert_protected_slices(old, new)
            ET.fromstring(after.read("word/document.xml"))
    except (BadZipFile, ET.ParseError, KeyError, RuntimeError) as exc:
        raise SpellcheckError("The spellchecked Word file could not be opened.") from exc


def _rezip(original: bytes, replacements: dict[str, bytes]) -> bytes:
    source = ZipFile(BytesIO(original))
    buffer = BytesIO()
    with ZipFile(buffer, "w") as target:
        for item in source.infolist():
            info = ZipInfo(filename=item.filename, date_time=item.date_time)
            info.compress_type = item.compress_type
            info.external_attr = item.external_attr
            info.create_system = item.create_system
            info.flag_bits = item.flag_bits & ~0x8
            target.writestr(info, replacements.get(item.filename, source.read(item.filename)))
    return buffer.getvalue()


def _render_report(changes: list[LineChange]) -> str:
    if not changes:
        return "No confident spelling corrections found.\n"
    corrections = sum(len(change.pairs) for change in changes)
    lines = [f"Changed lines: {len(changes)}", f"Word corrections: {corrections}", ""]
    for change in changes:
        lines.append(f"Location: {change.location}")
        lines.append(f"Original line: {change.original}")
        lines.append(f"Corrected line: {change.corrected}")
        lines.append("Corrections: " + "; ".join(f"{old} -> {new}" for old, new in change.pairs))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _publish(docx_path: Path, report_path: Path, docx: bytes, report: str) -> None:
    token = uuid.uuid4().hex
    temporary_docx = docx_path.with_name(f".{docx_path.name}.{token}.partial")
    temporary_report = report_path.with_name(f".{report_path.name}.{token}.partial")
    published_docx = False
    try:
        temporary_docx.write_bytes(docx)
        temporary_report.write_text(report, encoding="utf-8", newline="\n")
        _assert_package_safe(docx, temporary_docx.read_bytes(), set())
        os.replace(temporary_docx, docx_path)
        published_docx = True
        os.replace(temporary_report, report_path)
    except Exception:
        if published_docx and not report_path.is_file():
            docx_path.unlink(missing_ok=True)
        raise
    finally:
        temporary_docx.unlink(missing_ok=True)
        temporary_report.unlink(missing_ok=True)


def _remove_published(docx_path: Path, report_path: Path) -> None:
    docx_path.unlink(missing_ok=True)
    report_path.unlink(missing_ok=True)


def _frozen_spans(text: str) -> list[tuple[int, int]]:
    return [match.span() for match in _URL_RE.finditer(text)]


def _safe_stem(value: str) -> str:
    stem = Path(value).stem.strip().rstrip(".")
    stem = re.sub(r'[<>:"/\\|?*]', "_", stem)
    return stem[:80] or "exam"


def _local(element: ET.Element) -> str:
    return _split(element)[1]


def _split(element: ET.Element) -> tuple[str, str]:
    if element.tag.startswith("{") and "}" in element.tag:
        namespace, tag = element.tag[1:].split("}", 1)
        return namespace, tag
    return "", element.tag


def _lexicon() -> _Lexicon:
    global _LEX
    if _LEX is not None:
        return _LEX
    with _LEX_LOCK:
        if _LEX is None:
            _LEX = _load_lexicon()
    return _LEX


def _load_lexicon() -> _Lexicon:
    en_words = set(_gzip_text("en_words.txt.gz").splitlines())
    en_freq = {}
    for line in _gzip_text("en_freq.txt.gz").splitlines():
        word, freq = line.split()
        en_freq[word] = int(freq)
    hi_known = {unicodedata.normalize("NFC", word) for word in _gzip_text("hi_known.txt.gz").splitlines() if word}
    hi_freq = {}
    for line in _gzip_text("hi_words.txt.gz").splitlines():
        word, freq = line.rsplit(" ", 1)
        hi_freq[unicodedata.normalize("NFC", word)] = int(freq)
    hi_known.update(hi_freq)
    alphabet = "".join(sorted({char for word in hi_known for char in word}))
    return _Lexicon(en_words, en_freq, hi_known, hi_freq, alphabet)


def _gzip_text(name: str) -> str:
    path = DATA / name
    try:
        return gzip.decompress(path.read_bytes()).decode("utf-8")
    except OSError as exc:
        raise SpellcheckError("The spelling dictionaries are unavailable.") from exc
