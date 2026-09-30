"""Build the private pattern lists the pre-commit hook checks against.

    uv run python -m src.private_names

Writes three gitignored files under `<data_root>`, so none of them can be
committed:

    private-names.txt     place names, from filenames and media folders
    private-dates.txt     every entry date in the archive
    private-phrases.txt   episode wording, from the evaluation questions

Why this exists: writing code and documentation *about* a private archive means
reaching for its real details as the obvious illustration — a comment explaining
why a photo path failed to resolve, a test fixture, a worked example in the
README. Every category here leaked at least once before the hook caught it, and
each time it had to be spotted by eye.

The three lists exist because one list cannot express three matching rules:

    names     word-boundary, case-insensitive
    dates     exact, but never inside an ISO timestamp — a date rule once
              rewrote a PyPI `upload-time` in uv.lock, which has nothing to do
              with anyone's journal
    phrases   substring for multi-word episodes, word-boundary for single rare
              words

Anything derived from the archive belongs in a gitignored file, and the check
belongs in a hook rather than in someone's memory.
"""

from __future__ import annotations

import json
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
    # Real place names in this archive that are also ordinary English. Left in,
    # they fire on prose like "long entries" or "region-level labels" — and a
    # hook that cries wolf on every commit gets bypassed, which is worse than
    # not having one. The names they shadow are generic enough to reveal little.
    "long", "nice", "region", "district", "central", "national", "royal",
    "great", "little", "upper", "lower", "grand", "high", "middle", "point",
    "cross", "well", "wells", "bath", "reading", "stirling", "sandwich",
}

# Ordinary English that also turns up as a rare token in this archive. Blocking
# these would reject normal prose for no privacy gain.
NOT_IDENTIFYING = {
    "shows", "climbed", "married", "amount", "helped", "stayed", "gift",
    "dining", "glass", "broke", "change", "times", "place", "room", "went",
    "took", "time", "kind", "gave", "wrong", "lost", "flat", "tire", "bike",
    "shop", "phone", "travel", "someone", "restaurant", "hotel", "west",
}

FILENAME_RE = re.compile(r"^(?P<trip>.+?)\s*-\s*\d{4}$")

# A question word occurring in at most this many entries identifies a specific
# episode; above it, the word is describing a category. Chosen by measuring the
# real questions: the words that leaked sit at 3-12 entries, while the words
# that must stay usable — "tire" at 283, "bike" at 388 — are an order of
# magnitude commoner. Nothing in the eval set falls in between.
IDENTIFYING_MAX_ENTRIES = 15

STOP = frozenset("""a an the and or of in on at to for with i we my our it that this
was were is are be been had have has did do does from by as but so then there here
when where what which who how why all any some more most other into over under
about after before during while than too very just only also not no nor me us""".split())


def collect_names(raw: Path) -> set[str]:
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


def load_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def collect_dates(entries: list[dict]) -> set[str]:
    return {e["entry_date"] for e in entries if e.get("entry_date")}


def read_questions(path: Path) -> list[tuple[str, bool]]:
    """(question, is_absent) pairs. Parsed here rather than imported from
    evaluate so this stays a plain-stdlib script with no index dependency."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        question = parts[0]
        answers = parts[1] if len(parts) > 1 else ""
        out.append((question, answers.upper() == "NONE"))
    return out


def collect_phrases(questions: list[tuple[str, bool]],
                    entries: list[dict]) -> tuple[set[str], set[str]]:
    """(multi-word phrases, single identifying words) from the eval questions.

    Questions about things that never happened are skipped: they describe
    non-events, so they are not archive content and blocking them would reject
    the project's own documentation about refusal testing.
    """
    texts = [e.get("text", "").lower() for e in entries]

    def entries_containing(word: str) -> int:
        return sum(1 for t in texts if word in t)

    phrases: set[str] = set()
    words: set[str] = set()

    for q, absent in questions:
        if absent:
            continue
        phrases.add(q.strip().lower())

        tokens = re.findall(r"[a-z0-9']+", q.lower())
        content = [t for t in tokens if t not in STOP and len(t) > 3]

        # Adjacent content words in the original wording. A pair like "glass
        # orangery" identifies an episode even though each word alone is
        # ordinary and far too common to block on its own.
        for a, b in zip(tokens, tokens[1:]):
            if a in content and b in content:
                phrases.add(f"{a} {b}")

        for t in content:
            if t in NOT_IDENTIFYING or t in TOO_GENERIC:
                continue
            n = entries_containing(t)
            if 0 < n <= IDENTIFYING_MAX_ENTRIES:
                words.add(t)

    phrases = {p for p in phrases if len(p) > 8}
    return phrases, words


HEADER = ("# Derived from the archive. Gitignored — never commit this file.\n"
          "# The pre-commit hook blocks these in staged changes.\n"
          "# Regenerate after adding journals: uv run python -m src.private_names\n")


def main() -> None:
    root = CONFIG.paths.data_root
    entries = load_entries(CONFIG.paths.entries)
    questions = read_questions(CONFIG.paths.questions)

    names = collect_names(CONFIG.paths.raw)
    dates = collect_dates(entries)
    phrases, words = collect_phrases(questions, entries)

    (root / "private-names.txt").write_text(HEADER + "\n".join(sorted(names)) + "\n")
    (root / "private-dates.txt").write_text(HEADER + "\n".join(sorted(dates)) + "\n")
    (root / "private-phrases.txt").write_text(
        HEADER
        + "# Multi-word episode wording, matched as a case-insensitive substring.\n"
        + "\n".join(sorted(phrases))
        + "\n# --- single words, matched on word boundaries ---\n"
        + "\n".join(sorted(words)) + "\n")

    print(f"wrote pattern lists to {root}")
    print(f"  {len(names):>5} place names")
    print(f"  {len(dates):>5} entry dates")
    print(f"  {len(phrases):>5} episode phrases")
    print(f"  {len(words):>5} identifying words "
          f"(<= {IDENTIFYING_MAX_ENTRIES} entries each)")
    if not entries:
        print("  NOTE: no entries.jsonl — dates and phrases will be empty")
    print("  the pre-commit hook will now block all of these in staged changes")


if __name__ == "__main__":
    main()
