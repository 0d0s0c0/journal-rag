"""Verify image files are readable, and report which are not.

Written after finding 93 corrupt files in one folder of the archive — zero-length
and zero-filled JPEGs, the signature of an interrupted copy rather than gradual
corruption. Nothing in the pipeline would have noticed: a broken photo path just
produces a broken link months later.

Use it to check a source before copying, and to check the destination after.

    uv run python -m src.verify_images <dir>
    uv run python -m src.verify_images <dir> --list      # print bad filenames

Reports counts and filenames only — never image content.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".bmp", ".webp"}


def verify(root: Path) -> tuple[list[Path], int]:
    """Return (unreadable files, total checked)."""
    bad: list[Path] = []
    total = 0
    for f in sorted(root.rglob("*")):
        if f.suffix.lower() not in SUFFIXES or f.name.startswith("."):
            continue
        total += 1
        try:
            with Image.open(f) as im:
                im.verify()          # checks structure without decoding pixels
        except Exception:
            bad.append(f)
    return bad, total


def describe(f: Path) -> str:
    """Why a file failed, from its size and magic bytes — no content shown."""
    size = f.stat().st_size
    if size == 0:
        return "zero-length"
    head = f.open("rb").read(3)
    if head == b"\x00\x00\x00":
        return "zero-filled"
    if head[:2] == b"\xff\xd8":
        return "valid JPEG header, truncated or damaged body"
    return "not an image (wrong magic bytes)"


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    show = "--list" in sys.argv
    if len(args) != 1:
        sys.exit(__doc__)

    root = Path(args[0]).expanduser()
    if not root.is_dir():
        sys.exit(f"not a directory: {root}")

    bad, total = verify(root)
    ok = total - len(bad)
    print(f"{root}")
    print(f"  checked    : {total:,}")
    print(f"  readable   : {ok:,}")
    print(f"  unreadable : {len(bad):,}"
          + (f"  ({100 * len(bad) // total}%)" if total else ""))

    if bad:
        reasons: dict[str, int] = {}
        for f in bad:
            r = describe(f)
            reasons[r] = reasons.get(r, 0) + 1
        print("  causes     :")
        for r, n in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"      {n:5d}  {r}")
        by_dir: dict[Path, int] = {}
        for f in bad:
            by_dir[f.parent] = by_dir.get(f.parent, 0) + 1
        print("  by folder  :")
        for d, n in sorted(by_dir.items(), key=lambda x: -x[1])[:10]:
            print(f"      {n:5d}  {d.relative_to(root) if d != root else '.'}")
        if show:
            print("  files      :")
            for f in bad:
                print(f"      {f.relative_to(root)}")

    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
