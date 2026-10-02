"""Appearance for a selected Reference DOCX.

Only fields marked observed are returned. not_determined and not_set stay
empty so runtime does not invent a value for them.
"""

from __future__ import annotations

from dataclasses import dataclass

from reference_profile.sections import ReferenceProfileError, load_profile_for_reference


@dataclass(frozen=True)
class RoleStyle:
    font_name: str | None = None
    font_size_pt: float | None = None
    bold: bool | None = None
    underline: bool | None = None
    alignment: str | None = None
    left_indent_in: float | None = None
    space_before_pt: float | None = None
    space_after_pt: float | None = None


@dataclass(frozen=True)
class Appearance:
    page_width_twip: int | None
    page_height_twip: int | None
    orientation: str | None
    margin_top_twip: int | None
    margin_right_twip: int | None
    margin_bottom_twip: int | None
    margin_left_twip: int | None
    predominant_font: str | None
    default_size_pt: float | None
    line_spacing_multiple: float | None
    roles: dict[str, RoleStyle]
    header_lines: tuple[RoleStyle, ...]

    def role(self, name: str) -> RoleStyle:
        return self.roles.get(name) or RoleStyle()

    def font_for(self, role: RoleStyle | None) -> str | None:
        if role is not None and role.font_name:
            return role.font_name
        return self.predominant_font

    def size_for(self, role: RoleStyle | None) -> float | None:
        if role is not None and role.font_size_pt is not None:
            return role.font_size_pt
        return self.default_size_pt


def load_appearance(reference, profile_dir=None) -> Appearance:
    profile = load_profile_for_reference(reference, profile_dir)
    return appearance_from_profile(profile)


def appearance_from_profile(profile: dict) -> Appearance:
    appearance = profile.get("appearance") or {}
    source = profile.get("source_reference") or "reference"
    page = appearance.get("page") or {}
    margins = appearance.get("margins") or {}
    if page.get("status") != "observed" or margins.get("status") != "observed":
        raise ReferenceProfileError(
            f"Reference Profile for '{source}' has no observed page layout. "
            "Refusing to assume a page size or margins."
        )
    defaults = appearance.get("document_defaults") or {}
    header = profile.get("header") or {}
    lines = header.get("lines") or [] if header.get("status") == "observed" else []
    roles = appearance.get("roles") or {}
    return Appearance(
        page_width_twip=_int(page.get("width_twip")),
        page_height_twip=_int(page.get("height_twip")),
        orientation=page.get("orientation"),
        margin_top_twip=_int(margins.get("top_twip")),
        margin_right_twip=_int(margins.get("right_twip")),
        margin_bottom_twip=_int(margins.get("bottom_twip")),
        margin_left_twip=_int(margins.get("left_twip")),
        predominant_font=_observed(appearance.get("predominant_font")),
        default_size_pt=_observed(defaults.get("font_size_pt")),
        line_spacing_multiple=_line_multiple(defaults.get("line_spacing")),
        roles={name: _role_style(spec) for name, spec in roles.items() if isinstance(spec, dict)},
        header_lines=tuple(_role_style(line.get("formatting") or {}) for line in lines),
    )


def _role_style(spec: dict) -> RoleStyle:
    if spec.get("status") == "not_determined":
        return RoleStyle()
    return RoleStyle(
        font_name=_text(_observed(spec.get("font_name"))),
        font_size_pt=_float(_observed(spec.get("font_size_pt"))),
        bold=_bool(_observed(spec.get("bold"))),
        underline=_bool(_observed(spec.get("underline"))),
        alignment=_text(_observed(spec.get("alignment"))),
        left_indent_in=_float(_observed(spec.get("left_indent_in"))),
        space_before_pt=_float(_observed(spec.get("space_before_pt"))),
        space_after_pt=_float(_observed(spec.get("space_after_pt"))),
    )


def _line_multiple(node) -> float | None:
    if not isinstance(node, dict) or node.get("status") != "observed":
        return None
    multiple = node.get("multiple")
    if isinstance(multiple, (int, float)):
        return float(multiple)
    return None


def _observed(node):
    if isinstance(node, dict) and node.get("status") == "observed":
        return node.get("value")
    return None


def _text(value):
    return value if isinstance(value, str) and value else None


def _float(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _bool(value):
    if isinstance(value, bool):
        return value
    return None


def _int(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)
