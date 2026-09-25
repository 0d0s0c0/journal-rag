"""Build the private place-name list the pre-commit hook checks against.

    uv run python -m src.private_names

Writes `<data_root>/private-names.txt`, which is outside the repo and therefore
never committed. The hook reads it if present and blocks any commit containing
one of the names.

Why this exists: writing code and documentation *about* a private archive means
reaching for its real names as the obvious illustration — a comment explaining
why a photo path failed to resolve, a test fixture, a worked example in the
README. It happened three times in this repo before anyone noticed, and each
time it had to be caught by eye. Names derived from the archive belong in a
gitignored file, and the check belongs in a hook rather than in someone's
memory.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.config import CONFIG

# Words that appear in place names but are far too common to block on: a commit
# message saying "north" must not be rejected.
TOO_GENERIC = {
    "pictures", "photos", "photo", "video", "videos", "misc", "other", "trip",
    "north", "south", "east", "west", "city", "town", "park", "island",
    "beach", "lake", "river", "bay", "old", "new", "house", "home", "road",
    "street", "market", "museum", "hotel", "temple", "church", "castle",
}

FILENAME_RE = re.compile(r"^(?P<trip>.+?)\s*-\s*\d{4}$")


def collect(raw: Path) -> set[str]:
    names: set[str] = set()

    for f in raw.glob("*.doc*"):
        if f.name.startswith("~$"):
            continue
        if m := FILENAME_RE.match(f.stem):
            names.add(m.group("trip").strip().lower())

    for d in raw.rglob("*"):
        if d.is_dir() and not re.fullmatch(r"\d{4}", d.name):
            names.add(d.name.strip().lower())

    # Split multi-word labels so "mt vespugia" also blocks a bare "vespugia".
    names |= {w for n in names for w in re.split(r"[\s/_-]+", n) if len(w) > 3}
    return {n for n in names if len(n) > 3} - TOO_GENERIC


def main() -> None:
    out = CONFIG.paths.data_root / "private-names.txt"
    names = collect(CONFIG.paths.raw)
    out.write_text(
        "# Place names derived from the archive. Gitignored — never commit this.\n"
        "# The pre-commit hook blocks any staged change containing one.\n"
        "# Regenerate after adding journals: uv run python -m src.private_names\n"
        + "\n".join(sorted(names)) + "\n"
    )
    print(f"wrote {out}")
    print(f"  {len(names)} names")
    print("  the pre-commit hook will now block these in staged changes")


if __name__ == "__main__":
    main()
