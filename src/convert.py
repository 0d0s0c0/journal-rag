"""Convert journal .docx files into one structured JSONL of entries.

Deterministic parsing only — no model, no chunking, no extraction. Everything
downstream reads the JSONL and never touches a .docx again.

    uv run python -m src.convert                 # raw/ -> text/entries.jsonl
    uv run python -m src.convert --dry-run       # report counts, write nothing

Every rule here traces to something measured in the real archive; see
docs/CORPUS.md. The important ones:

  * A date line starts an entry. Whatever else is on that line is body text.
  * Dates can hide after a soft line break (Shift+Enter) inside a paragraph.
    A paragraph-level scan misses them and the entry vanishes entirely.
  * The filename year is the year the trip STARTED. Dec->Jan travel is common,
    so the year increments when the month moves backwards.
  * Inline photo references are ~30% of all characters. Left in the text they
    dominate the embeddings, so they are stripped out and kept as metadata.

Per-entry `warnings` record what happened rather than deciding silently, so
anything questionable can be grepped for and checked by hand.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn

from src.dates import DATE_RE, parse_date_line

MEDIA_SUFFIXES = {
    "jpg", "jpeg", "png", "heic", "heif", "gif", "tif", "tiff",
    "mov", "mp4", "m4v", "avi",
}

# Inline photo references as written in the text, e.g.
#   (2013\syldavia\vale temple - root.jpg, 2013\syldavia\vale temple - root2.jpg)
#   (strelsau\old bridge - looking south.jpg)
# Mixed separators occur. The trailing group swallows the comma-separated run so
# a whole parenthetical is removed in one go rather than leaving stray commas.
_MEDIA_ALT = "|".join(sorted(MEDIA_SUFFIXES))
PHOTO_REF = re.compile(
    rf"\(?\s*[^()\n]*?\.(?:{_MEDIA_ALT})"
    rf"(?:\s*,\s*[^()\n]*?\.(?:{_MEDIA_ALT}))*\s*\)?",
    re.IGNORECASE,
)

# "<place> - <year>.docx" -> trip label and the year the trip started.
FILENAME_RE = re.compile(r"^(?P<trip>.+?)\s*-\s*(?P<year>\d{4})\s*$")


@dataclass
class Entry:
    id: str
    source_file: str
    trip: str | None
    entry_date: str | None          # ISO, or None when the year is unknown
    month_day: str                  # always present, e.g. "02-13"
    text: str
    photos: list[dict] = field(default_factory=list)
    chars: int = 0
    warnings: list[str] = field(default_factory=list)


# ── document reading ─────────────────────────────────────────────────────────

def _lines_with_links(path: Path) -> list[tuple[str, list[str]]]:
    """Flatten a document to (line, [photo link targets]) pairs.

    Paragraphs are split on soft line breaks, because a date typed after
    Shift+Enter lives inside a paragraph as '\\n' and is invisible to a
    paragraph-level scan. Links are attributed to the paragraph's first line;
    finer placement is not needed and would be guesswork.
    """
    doc = Document(str(path))
    rels = doc.part.rels
    out: list[tuple[str, list[str]]] = []

    for par in doc.paragraphs:
        targets: list[str] = []
        for h in par._p.findall(qn("w:hyperlink")):
            rid = h.get(qn("r:id"))
            if not rid or rid not in rels:
                continue
            rel = rels[rid]
            if rel.reltype != RT.HYPERLINK:
                continue
            if rel.target_ref.rsplit(".", 1)[-1].split("%")[0].lower() in MEDIA_SUFFIXES:
                targets.append(rel.target_ref)

        segments = (par.text or "").split("\n")
        for i, seg in enumerate(segments):
            out.append((seg, targets if i == 0 else []))
    return out


# ── text cleanup ─────────────────────────────────────────────────────────────

def strip_photo_refs(text: str) -> tuple[str, list[str]]:
    """Remove inline photo references, returning (clean text, refs found)."""
    found: list[str] = []

    def _take(m: re.Match) -> str:
        found.append(m.group(0))
        return " "

    cleaned = PHOTO_REF.sub(_take, text)
    cleaned = re.sub(r"\s*\(\s*\)", "", cleaned)        # emptied parentheses
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)  # space before punctuation
    return cleaned, found


def normalise(text: str) -> str:
    """Collapse the double spacing between sentences; keep paragraph breaks."""
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def normalise_path(target: str) -> str:
    """Reduce a link target to <year>/<place>/<file>, or <place>/<file>.

    Word stored whatever path it was given — relative, Windows absolute,
    file:// URL, mixed separators. Parsing it ourselves means a stale
    C:\\ path from another machine still re-roots onto the local archive.
    """
    from urllib.parse import unquote

    t = unquote(target).replace("file:///", "").replace("file://", "")
    parts = re.split(r"[\\/]+", t)
    parts = [p for p in parts if p and p not in ("..", ".") and not p.endswith(":")]
    return "/".join(parts[-3:])


# ── the parse ────────────────────────────────────────────────────────────────

def convert_file(path: Path) -> tuple[list[Entry], list[str]]:
    """Parse one journal into entries plus file-level warnings."""
    file_warnings: list[str] = []
    stem = path.stem
    m = FILENAME_RE.match(stem)
    trip = m.group("trip").strip() if m else stem
    base_year = int(m.group("year")) if m else None
    if base_year is None:
        file_warnings.append("no_year_in_filename")

    lines = _lines_with_links(path)

    # Locate entry starts. A date line begins an entry; anything after the date
    # on the same line is the first body text.
    starts: list[tuple[int, tuple[int, int, int | None], str]] = []
    for i, (line, _) in enumerate(lines):
        text = line.strip()
        if not text:
            continue
        parsed = parse_date_line(text)
        if parsed:
            starts.append((i, parsed, ""))
            continue
        # date followed by body on the same line
        head = re.match(r"^\s*([A-Za-z]+\.?\s*\d{1,2}(?:st|nd|rd|th)?)\b[\s.,:-]*(.*)$", text)
        if head and head.group(2):
            p2 = parse_date_line(head.group(1))
            if p2:
                starts.append((i, p2, head.group(2)))

    if not starts:
        file_warnings.append("no_date_lines")
        return [], file_warnings

    entries: list[Entry] = []
    year = base_year
    prev_month: int | None = None

    # Text before the first date line would otherwise be silently dropped.
    if any(ln.strip() for ln, _ in lines[: starts[0][0]]):
        entries.append(_build(
            idx=0, stem=stem, trip=trip, source=path.name,
            month=None, day=None, year=None, inline="",
            block=lines[: starts[0][0]], warnings=["preamble"],
        ))

    bounds = [s[0] for s in starts] + [len(lines)]
    for n, ((start, (month, day, inline_year), inline), end) in enumerate(
        zip(starts, bounds[1:])
    ):
        warnings: list[str] = []
        if inline_year:
            year = inline_year
            warnings.append("year_from_entry")
        elif year is not None and prev_month is not None and month < prev_month:
            # Only a genuine New Year crossing increments the year. "Month moved
            # backwards" alone is too loose — a file covering two trips in one
            # year (October, then a June trip) trips it, and because the year is
            # carried forward, one false positive misdates every entry after it.
            crosses_new_year = prev_month >= 11 and month <= 2
            within_file_limit = base_year is None or year + 1 <= base_year + 1
            if crosses_new_year and within_file_limit:
                year += 1
                warnings.append("year_rollover")
            else:
                # Out of chronological order for some other reason. Keep the
                # year as-is and flag it rather than guessing.
                warnings.append("out_of_order")
        prev_month = month
        if inline:
            warnings.append("date_on_same_line")

        entries.append(_build(
            idx=n + 1, stem=stem, trip=trip, source=path.name,
            month=month, day=day, year=year, inline=inline,
            block=lines[start + 1: end], warnings=warnings,
        ))

    _flag_date_outliers(entries)
    return entries, file_warnings


def _flag_date_outliers(entries: list[Entry]) -> None:
    """Flag entries whose date breaks the file's chronology.

    These are typos in the source, not parser errors: a month typed wrong while
    the day continues the sequence — "07-02, 06-03, 07-04" should plainly read
    07-03. Left alone, that entry answers questions about the wrong month.

    Where substituting a neighbour's month restores the order, the suspected
    correction is recorded. Nothing is rewritten: the journals are the record,
    and a wrong auto-correction is worse than a flagged oddity.
    """
    dated = [e for e in entries if e.entry_date]
    d = [e.entry_date for e in dated]

    for i in range(1, len(d)):
        if d[i] == d[i - 1]:
            dated[i].warnings.append("duplicate_date")   # normal; two entries, one day
            continue
        if d[i] > d[i - 1]:
            continue                                     # in order

        # A step backwards. Decide which of the pair is the outlier by asking
        # which one, removed, restores local order.
        before = d[i - 2] if i >= 2 else None
        after = d[i + 1] if i + 1 < len(d) else None
        drop_current = (before is None or before <= d[i - 1]) and \
                       (after is None or d[i - 1] <= after)
        culprit = dated[i] if drop_current else dated[i - 1]
        culprit.warnings.append("date_out_of_sequence")

        # If swapping in a neighbour's month restores order, it is almost
        # certainly a typed-month slip. Recorded as a suggestion only —
        # the journals are the record, and a wrong auto-correction is worse
        # than a flagged oddity.
        lo = before if culprit is dated[i - 1] else d[i - 1]
        hi = after if culprit is dated[i] else d[i]
        day = culprit.entry_date[8:]
        for donor in (d[i - 1], d[i]):
            cand = f"{donor[:8]}{day}"
            if cand != culprit.entry_date and (lo is None or lo < cand) \
               and (hi is None or cand < hi):
                culprit.warnings.append(f"suspect_month_typo:{cand}")
                break


def _build(*, idx, stem, trip, source, month, day, year, inline, block, warnings):
    raw_lines = ([inline] if inline else []) + [ln for ln, _ in block]
    targets = [t for _, ts in block for t in ts]

    clean, inline_refs = strip_photo_refs("\n".join(raw_lines))
    text = normalise(clean)

    photos = [{"path": normalise_path(t), "source": "link"} for t in targets]
    seen = {p["path"] for p in photos}
    for ref in inline_refs:
        for part in re.split(r"\s*,\s*", ref.strip("() ")):
            if "." not in part:
                continue
            p = normalise_path(part)
            if p and p not in seen:
                seen.add(p)
                photos.append({"path": p, "source": "inline"})

    if not photos:
        warnings = warnings + ["no_photos"]

    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
    date_iso = f"{year:04d}-{month:02d}-{day:02d}" if (year and month) else None
    return Entry(
        id=f"{slug}#{idx:04d}",
        source_file=source,
        trip=trip,
        entry_date=date_iso,
        month_day=f"{month:02d}-{day:02d}" if month else "",
        text=text,
        photos=photos,
        chars=len(text),
        warnings=warnings,
    )


# ── driver ───────────────────────────────────────────────────────────────────

def load_corrections(path: Path) -> dict[str, str]:
    """Read `entry-id: YYYY-MM-DD` overrides, one per line.

    Journals contain occasional mistyped date headers — a month slip while the
    day continues the sequence. Rather than editing the source documents (which
    risks the hyperlink relationships, and means unlocking a read-only archive),
    corrections live in a separate file that is applied at conversion time.
    Reviewable, reversible, and the originals remain the record.
    """
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for n, line in enumerate(path.read_text().splitlines(), 1):
        line = line.split("#!")[0].strip() if "#!" in line else line.strip()
        if not line or line.startswith("//"):
            continue
        if ":" not in line:
            print(f"  corrections:{n}: skipped, no ':' -> {line!r}")
            continue
        key, _, val = line.partition(":")
        val = val.strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", val):
            print(f"  corrections:{n}: skipped, not YYYY-MM-DD -> {val!r}")
            continue
        out[key.strip()] = val
    return out


def apply_corrections(entries: list[Entry], corrections: dict[str, str]) -> set[str]:
    """Apply date overrides; return the ids that matched."""
    used: set[str] = set()
    for e in entries:
        new = corrections.get(e.id)
        if not new:
            continue
        used.add(e.id)
        e.warnings = [w for w in e.warnings
                      if not w.startswith(("date_out_of_sequence", "suspect_month_typo"))]
        e.warnings.append(f"date_corrected:from={e.entry_date}")
        e.entry_date = new
        e.month_day = new[5:]
    return used


def main() -> None:
    dry = "--dry-run" in sys.argv
    root = Path.home() / "playground/journal-data"
    raw, out_dir = root / "raw", root / "text"
    files = sorted(f for f in raw.glob("*.doc*") if not f.name.startswith("~$"))
    if not files:
        sys.exit(f"no journals found in {raw}")

    all_entries: list[Entry] = []
    per_file: list[tuple[str, int, int, list[str]]] = []
    for f in files:
        try:
            entries, fw = convert_file(f)
        except Exception as e:                    # keep going across the archive
            per_file.append((f.name, 0, 0, [f"ERROR {type(e).__name__}"]))
            continue
        all_entries.extend(entries)
        per_file.append((f.name, len(entries), sum(len(e.photos) for e in entries), fw))

    corrections = load_corrections(root / "corrections.txt")
    if corrections:
        used = apply_corrections(all_entries, corrections)
        missing = set(corrections) - used
        print(f"corrections        : {len(used)} applied"
              + (f", {len(missing)} unmatched: {sorted(missing)}" if missing else ""))

        # Re-derive the sequence flags. Correcting an entry can resolve its
        # neighbours too, and a warning that is no longer true is worse than no
        # warning — it sends you looking for a problem that has been fixed.
        by_source: dict[str, list[Entry]] = {}
        for e in all_entries:
            by_source.setdefault(e.source_file, []).append(e)
        for group in by_source.values():
            for e in group:
                e.warnings = [w for w in e.warnings
                              if not w.startswith(("date_out_of_sequence",
                                                   "suspect_month_typo",
                                                   "duplicate_date"))]
            _flag_date_outliers(group)

    counts: dict[str, int] = {}
    for e in all_entries:
        for w in e.warnings:
            counts[w] = counts.get(w, 0) + 1

    print(f"files              : {len(files)}")
    print(f"entries            : {len(all_entries)}")
    print(f"photos linked      : {sum(len(e.photos) for e in all_entries):,}")
    print(f"text chars         : {sum(e.chars for e in all_entries):,} "
          f"(~{sum(e.chars for e in all_entries)//4:,} tokens)")
    print(f"entries w/o a date : {sum(1 for e in all_entries if not e.entry_date)}")
    print("warnings           :")
    for w, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"    {n:6d}  {w}")

    problems = [(n, fw) for n, _, _, fw in per_file if fw]
    if problems:
        print("file-level issues  :")
        for n, fw in problems:
            print(f"    {n}: {', '.join(fw)}")

    if dry:
        print("\n--dry-run: nothing written")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "entries.jsonl"
    with out.open("w") as fh:
        for e in all_entries:
            fh.write(json.dumps(asdict(e), ensure_ascii=False) + "\n")

    man = out_dir / "manifest.csv"
    with man.open("w") as fh:
        fh.write("source_file,entries,photos,file_warnings\n")
        for n, ne, np, fw in per_file:
            fh.write(f'"{n}",{ne},{np},"{";".join(fw)}"\n')
    print(f"\nwrote {out}\nwrote {man}")


if __name__ == "__main__":
    main()
