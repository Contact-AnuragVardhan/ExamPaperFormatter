"""Header requirements for a selected Reference DOCX.

Observed header roles come from the Reference Profile, in profile order.
A line whose role was not determined is not a conformance requirement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from reference_profile.sections import ReferenceProfileError, load_profile_for_reference


@dataclass(frozen=True)
class HeaderRequirement:
    order: int
    role: str
    label: str
    pattern: re.Pattern

    def matches(self, text: str) -> bool:
        return self.pattern.search(" ".join((text or "").split())) is not None


def load_header_policy(reference, profile_dir=None) -> list[HeaderRequirement]:
    """Required header roles for this Reference DOCX, in profile order."""
    profile = load_profile_for_reference(reference, profile_dir)
    return header_policy_from_profile(profile)


def header_policy_from_profile(profile: dict) -> list[HeaderRequirement]:
    header = profile.get("header") or {}
    source = profile.get("source_reference") or "reference"
    if header.get("status") != "observed":
        raise ReferenceProfileError(
            f"Reference Profile for '{source}' has no observed header. "
            "Refusing to assume header requirements."
        )
    requirements: list[HeaderRequirement] = []
    seen: set[str] = set()
    for line in header.get("lines") or []:
        role = line.get("role") or {}
        if role.get("status") != "observed":
            continue
        name = role.get("value")
        label = role.get("label")
        pattern = role.get("pattern")
        if not isinstance(name, str) or not name or name in seen:
            continue
        if not isinstance(label, str) or not label or not isinstance(pattern, str) or not pattern:
            raise ReferenceProfileError(
                f"Reference Profile for '{source}' has header role '{name}' without a recognition pattern. "
                "Refusing to assume how that header is recognized."
            )
        seen.add(name)
        requirements.append(
            HeaderRequirement(
                order=int(line.get("order") or len(requirements) + 1),
                role=name,
                label=label,
                pattern=re.compile(pattern, re.IGNORECASE),
            )
        )
    return requirements
