"""Build a Reference Profile from a Reference DOCX.

This does not format a teacher exam and does not change the runtime path.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reference_profile import write_profile


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a Reference Profile from a Reference DOCX")
    parser.add_argument("reference", type=Path, help="Reference DOCX to inspect")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data" / "reference_profiles",
        help="Directory for the profile JSON",
    )
    args = parser.parse_args(argv)
    if not args.reference.is_file():
        print(f"Reference DOCX not found: {args.reference}", file=sys.stderr)
        return 1
    target = write_profile(args.reference, args.output_dir)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
