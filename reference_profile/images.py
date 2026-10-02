"""Image policy for a selected Reference DOCX.

The saved Reference Profile supplies how many images the exam is expected
to contain. Runtime does not assume that every exam has an image.
"""

from __future__ import annotations

from dataclasses import dataclass

from reference_profile.sections import ReferenceProfileError, load_profile_for_reference


@dataclass(frozen=True)
class ImagePolicy:
    """Expected images from the profile. Placement stays with the teacher file."""

    count: int
    items: tuple


def load_image_policy(reference, profile_dir=None) -> ImagePolicy:
    """Policy for this Reference DOCX. Raises if its profile is missing."""
    profile = load_profile_for_reference(reference, profile_dir)
    return image_policy_from_profile(profile)


def image_policy_from_profile(profile: dict) -> ImagePolicy:
    images = profile.get("images") or {}
    count = images.get("count")
    items = images.get("items") or []
    if images.get("status") != "observed" or not isinstance(count, int) or count < 0 or len(items) != count:
        source = profile.get("source_reference") or "reference"
        raise ReferenceProfileError(
            f"Reference Profile for '{source}' has no observed image count. "
            "Refusing to assume an image requirement."
        )
    return ImagePolicy(count=count, items=tuple(items))
