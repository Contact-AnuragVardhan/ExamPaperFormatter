"""Section policy for a selected Reference DOCX.

The saved Reference Profile supplies the section names and their order.
Runtime code must not invent a section sequence when that profile is missing
or belongs to a different file.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

PROFILE_DIR = Path(__file__).resolve().parents[1] / "data" / "reference_profiles"
_DASH = re.compile(r"^[-–—]+$")


class ReferenceProfileError(Exception):
    """The selected Reference DOCX has no usable matching Reference Profile."""


def heading_key(text: str) -> tuple[str, ...]:
    """Compare headings while ignoring spaces and hyphens."""
    normalized = (text or "").replace("–", "-").replace("—", "-")
    tokens: list[str] = []
    for token in normalized.split():
        if _DASH.fullmatch(token):
            continue
        for piece in token.split("-"):
            if piece:
                tokens.append(piece.casefold())
    return tuple(tokens)


def canonical_heading(text: str, headings: list[str]) -> str | None:
    """Return the profile heading this line is, if it is one of them."""
    key = heading_key(text)
    if not key:
        return None
    for heading in headings:
        if heading_key(heading) == key:
            return heading
    return None


def contains_heading(text: str, heading: str) -> bool:
    """True when the heading's tokens appear in order, not as a prefix of a longer token."""
    wanted = heading_key(heading)
    if not wanted:
        return False
    haystack = heading_key(text)
    width = len(wanted)
    return any(haystack[index : index + width] == wanted for index in range(len(haystack) - width + 1))


def load_profile_for_reference(reference: str | Path, profile_dir: Path | None = None) -> dict:
    """Load the profile whose stored hash is the hash of this Reference DOCX."""
    path = Path(reference)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    directory = Path(profile_dir) if profile_dir is not None else PROFILE_DIR
    if not directory.is_dir():
        raise ReferenceProfileError(
            f"No Reference Profile matches '{path.name}' (sha256 {digest}). "
            "Refusing to assume a section sequence."
        )
    matches = []
    for candidate in sorted(directory.glob("*.json")):
        try:
            profile = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        recorded = profile.get("source_hash") or {}
        if recorded.get("algorithm") == "sha256" and recorded.get("hex") == digest:
            matches.append(profile)
    if not matches:
        raise ReferenceProfileError(
            f"No Reference Profile matches '{path.name}' (sha256 {digest}). "
            "Refusing to assume a section sequence."
        )
    if len(matches) > 1:
        headings = [_heading_list(profile, path) for profile in matches]
        if any(item != headings[0] for item in headings[1:]):
            raise ReferenceProfileError(
                f"More than one Reference Profile matches '{path.name}' and their section sequences differ."
            )
    return matches[0]


def required_section_headings(reference: str | Path, profile_dir: Path | None = None) -> list[str]:
    """Section headings in profile order for this Reference DOCX."""
    profile = load_profile_for_reference(reference, profile_dir)
    return _heading_list(profile, Path(reference))


def _heading_list(profile: dict, path: Path) -> list[str]:
    sections = (profile.get("structure") or {}).get("sections") or {}
    items = sections.get("items") or []
    if sections.get("status") != "observed" or not items:
        raise ReferenceProfileError(
            f"Reference Profile for '{path.name}' has no observed section sequence. "
            "Refusing to assume a section sequence."
        )
    headings = []
    for item in items:
        heading = item.get("heading")
        if not isinstance(heading, str) or not heading.strip():
            raise ReferenceProfileError(
                f"Reference Profile for '{path.name}' has a section without a heading."
            )
        headings.append(heading.strip())
    return headings
