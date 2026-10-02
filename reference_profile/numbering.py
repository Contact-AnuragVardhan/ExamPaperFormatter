"""Numbering policy for a selected Reference DOCX.

Observed patterns come from the Reference Profile. They describe the
Reference DOCX, not the labels the production formatter currently prints.

One observed signature does not match that production output. For that
signature only, output uses a compatibility policy. The compatibility
templates are not measurements and must not be written back into a profile
as if the Reference contained them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from reference_profile.sections import load_profile_for_reference

_ROMAN = (
    (10, "x"),
    (9, "ix"),
    (5, "v"),
    (4, "iv"),
    (1, "i"),
)
_ROMAN_PATTERN = r"xii|xi|x|ix|viii|vii|vi|v|iv|iii|ii|i"
_PLACEHOLDERS = (
    ("{n}", r"\d{1,2}"),
    ("{roman}", _ROMAN_PATTERN),
    ("{letter}", r"[a-z]"),
    ("{Letter}", r"[A-Z]"),
)

# Observed English signature. Compared against the profile; not a subject name.
_COMPATIBILITY_OBSERVED = {
    "major": ("Q{n}.", "Q{n}({letter})."),
    "subquestion": ("({roman})",),
    "choice": ("({letter})", "{Letter})"),
    "branch": ("Q{n}({letter}).",),
}
# Labels the current production formatter emits for that signature.
_COMPATIBILITY_OUTPUT = {
    "major": "Q{n})",
    "branch": "{Letter})",
    "subquestion": "{roman})",
    "choice": "{letter})",
}
_COMPATIBILITY_LEADING = re.compile(
    r"^\s*(?:"
    r"Q\d{1,2}\)\s*"
    r"|\d{1,2}[\.\)]\s*(?:\([A-Za-z]\)[\.\)]?\s*)?"
    r"|(?:xii|xi|x|ix|viii|vii|vi|v|iv|iii|ii|i)[\.\)]\s*"
    r"|\([A-Da-d]\)[\.\)]?\s*"
    r"|[A-Da-d][\.\)]\s*"
    r"|\d{1,2}\s+(?=[A-Za-z])"
    r")"
)


@dataclass(frozen=True)
class NumberingPolicy:
    """Templates used to print labels. None means that role is not imposed."""

    major: str | None
    branch: str | None
    subquestion: str | None
    choice: str | None
    source: str
    carrier_splits_branch: bool
    # None when the profile does not observe an alternative marker.
    # The compatibility signature supplies the production marker because
    # that reference records the role as not determined.
    alternative: str | None
    _leading: re.Pattern

    def label(self, role: str, index: int) -> str | None:
        pattern = getattr(self, role)
        if not pattern:
            return None
        return render_pattern(pattern, index)

    def strip_leading(self, text: str) -> str:
        return self._leading.sub("", text or "", count=1).strip()


def render_pattern(pattern: str, index: int) -> str:
    letter = _letter(index)
    return (
        pattern.replace("{n}", str(index))
        .replace("{roman}", _roman(index))
        .replace("{Letter}", letter.upper())
        .replace("{letter}", letter)
    )


def load_numbering_policy(reference, profile_dir=None) -> NumberingPolicy:
    """Policy for this Reference DOCX. Raises if its profile is missing."""
    profile = load_profile_for_reference(reference, profile_dir)
    return policy_from_profile(profile)


def policy_from_profile(profile: dict) -> NumberingPolicy:
    observed = _observed_patterns(profile.get("numbering") or {})
    if observed == _COMPATIBILITY_OBSERVED:
        return NumberingPolicy(
            major=_COMPATIBILITY_OUTPUT["major"],
            branch=_COMPATIBILITY_OUTPUT["branch"],
            subquestion=_COMPATIBILITY_OUTPUT["subquestion"],
            choice=_COMPATIBILITY_OUTPUT["choice"],
            source="compatibility",
            carrier_splits_branch=True,
            alternative="OR",
            _leading=_COMPATIBILITY_LEADING,
        )
    patterns = [item for item in (observed["major"], observed["subquestion"], observed["choice"], observed["branch"]) for item in item]
    parts = [f"(?:{template_to_regex(pattern)})" for pattern in patterns]
    leading = re.compile(r"^\s*(?:" + "|".join(parts) + r")") if parts else re.compile(r"(?!)")
    return NumberingPolicy(
        major=_plain(observed["major"]),
        branch=_plain(observed["branch"]),
        subquestion=_plain(observed["subquestion"]),
        choice=_plain(observed["choice"]),
        source="observed",
        carrier_splits_branch=False,
        alternative=_alternative_marker(profile),
        _leading=leading,
    )


def observed_patterns(profile: dict) -> dict[str, tuple[str, ...]]:
    return _observed_patterns(profile.get("numbering") or {})


def template_to_regex(pattern: str) -> str:
    index = 0
    pieces: list[str] = []
    while index < len(pattern):
        for token, regex in _PLACEHOLDERS:
            if pattern.startswith(token, index):
                pieces.append(regex)
                index += len(token)
                break
        else:
            pieces.append(re.escape(pattern[index]))
            index += 1
    return "".join(pieces) + r"\s*"


def _observed_patterns(numbering: dict) -> dict[str, tuple[str, ...]]:
    return {
        "major": _pattern_list(numbering.get("major")),
        "subquestion": _pattern_list(numbering.get("subquestion")),
        "choice": _pattern_list(numbering.get("choice")),
        "branch": _pattern_list(numbering.get("branch")),
    }


def _pattern_list(node) -> tuple[str, ...]:
    if not isinstance(node, dict) or node.get("status") != "observed":
        return ()
    found: list[str] = []
    if node.get("patterns"):
        found.extend(node["patterns"])
    for kind in node.get("kinds") or []:
        found.extend(kind.get("patterns") or [])
    return tuple(found)


def _alternative_marker(profile: dict) -> str | None:
    node = (profile.get("numbering") or {}).get("alternative") or {}
    if node.get("status") != "observed":
        return None
    value = node.get("value")
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                return item.strip()
    return None


def _plain(patterns: tuple[str, ...]) -> str | None:
    plain = [pattern for pattern in patterns if "{letter}" not in pattern and "{Letter}" not in pattern]
    if plain:
        return plain[0]
    return patterns[0] if patterns else None


def _roman(n: int) -> str:
    out = []
    for value, glyph in _ROMAN:
        while n >= value:
            out.append(glyph)
            n -= value
    return "".join(out)


def _letter(n: int) -> str:
    chars = []
    while n > 0:
        n, rem = divmod(n - 1, 26)
        chars.append(chr(ord("a") + rem))
    return "".join(reversed(chars))
