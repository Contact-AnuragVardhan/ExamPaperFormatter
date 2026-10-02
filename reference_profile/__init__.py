"""Reference Profile builder and the policies runtime reads from it.

Inspects a Reference DOCX and writes a profile. Runtime reads a saved
profile whose hash matches the selected Reference DOCX. It does not
rebuild a profile, and it does not assume a section sequence, a
numbering scheme, an image count, a header role list, or a page layout.
"""

from reference_profile.preprocess import PROFILE_VERSION, build_profile, write_profile

__all__ = ["PROFILE_VERSION", "build_profile", "write_profile"]
