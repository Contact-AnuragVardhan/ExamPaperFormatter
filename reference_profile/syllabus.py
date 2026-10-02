"""Syllabus heading for a selected Reference DOCX.

The heading text is copied from the profile when that profile observed one.
A profile with no syllabus does not invent a heading.
"""

from __future__ import annotations

from dataclasses import dataclass

from reference_profile.sections import load_profile_for_reference


@dataclass(frozen=True)
class SyllabusPolicy:
    heading: str | None
    location: str | None


def load_syllabus_policy(reference, profile_dir=None) -> SyllabusPolicy:
    profile = load_profile_for_reference(reference, profile_dir)
    return syllabus_from_profile(profile)


def syllabus_from_profile(profile: dict) -> SyllabusPolicy:
    node = (profile.get("structure") or {}).get("syllabus") or {}
    if node.get("status") != "observed":
        return SyllabusPolicy(heading=None, location=None)
    heading = node.get("heading")
    location = node.get("location")
    if not isinstance(heading, str) or not heading.strip():
        return SyllabusPolicy(heading=None, location=None)
    return SyllabusPolicy(
        heading=heading.strip(),
        location=location if isinstance(location, str) else None,
    )
