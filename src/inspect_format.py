"""Probe journal .doc/.docx files for structure without disclosing content.

Date lines are the only structure in this archive: one starts a new entry, and
everything else is text. So the questions that matter are whether every date
line is detectable, and whether any are hidden where a paragraph scan will miss
them — a missed date does not error, it silently merges two entries under the
wrong date.

Prints counts, shape descriptions and character lengths only. No journal text is
emitted, so the output is safe to share.

    uv run python -m src.inspect_format "<file.docx>"
    uv run python -m src.inspect_format "<dir>"     # every .doc/.docx within

Legacy .doc is converted via macOS textutil; originals are never modified.

Output is safe to share.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

# "Jun 14", "Jun 4", "Jun 14." — month abbreviation then day, nothing else.
MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"

# Strict: a month abbreviation and a day, and nothing else on the line.
DATE_RE = re.compile(rf"^\s*({MONTHS})\s+(\d{{1,2}})\s*\.?\s*$", re.IGNORECASE)

# Starts like a date but carries more on the line (e.g. a same-line title).
NEAR_RE = re.compile(rf"^\s*({MONTHS})[a-z]*\.?\s+(\d{{1,2}})\b", re.IGNORECASE)

# Begins with a month word but no day followed — e.g. "June" alone, "Jun 3rd".
MONTHWORD_RE = re.compile(rf"^\s*({MONTHS})[a-z]*\b", re.IGNORECASE)


def _fmt(par) -> str:
    """Compact formatting signature of a paragraph: style + bold/italic/size."""
    bits = [par.style.name if par.style is not None else "?"]
    runs = [r for r in par.runs if (r.text or "").strip()]
    if runs:
        if all(r.bold for r in runs):
            bits.append("bold")
        if all(r.italic for r in runs):
            bits.append("italic")
        sizes = {r.font.size.pt for r in runs if r.font.size is not None}
        if len(sizes) == 1:
            bits.append(f"{sizes.pop():g}pt")
    return "+".join(str(b) for b in bits)


def probe(path: Path, display: str | None = None) -> None:
    doc = Document(str(path))
    pars = doc.paragraphs

    date_idx = [i for i, p in enumerate(pars) if DATE_RE.match(p.text or "")]
    nonempty = [p for p in pars if (p.text or "").strip()]

    print(f"\n=== {display or path.name} ===")
    print(f"paragraphs        : {len(pars)}  ({len(nonempty)} non-empty)")
    print(f"date lines found  : {len(date_idx)}")

    if not date_idx:
        print("  !! no 'MMM DD' lines matched — format differs from expectation")
        styles = Counter(_fmt(p) for p in nonempty)
        print(f"  styles present  : {dict(styles.most_common(6))}")
        return

    # THE critical check. Date lines are now the ONLY structure in the archive,
    # so a date the regex misses does not raise an error — it silently merges
    # two entries and attributes both to the earlier date. Look for lines that
    # begin like a date but failed the strict match, and describe their SHAPE
    # (never their text) so the pattern can be extended if needed.
    near: Counter[str] = Counter()
    for p in nonempty:
        t = (p.text or "").strip()
        if DATE_RE.match(t):
            continue
        m = NEAR_RE.match(t)
        if m:
            rest = t[m.end():].strip(" .,-–—:")
            if rest:
                near[f"month+day followed by {len(rest.split())} more word(s) "
                     f"(same-line title?)"] += 1
            elif len(m.group(1)) != len(t.split()[0].rstrip(".")):
                near["full month name, e.g. 'June 16' not 'Jun 16'"] += 1
            else:
                near["month+day with unexpected trailing characters"] += 1
        elif MONTHWORD_RE.match(t) and len(t.split()) <= 6:
            near["month word but day not matched, e.g. ordinal '17th'"] += 1
    if near:
        print(f"  !! {sum(near.values())} near-miss date line(s) — would MERGE entries:")
        for shape, n in near.most_common(5):
            print(f"       {n:4d}x  {shape}")
    else:
        print("near-miss dates   : none — regex appears to catch every date line")

    # Soft line breaks (Shift+Enter) live INSIDE a paragraph as '\n'. A date on
    # such a line is invisible to a paragraph-based scan, so the entry vanishes.
    soft = sum(1 for p in pars if "\n" in (p.text or ""))
    soft_dates = sum(
        1 for p in pars
        for line in (p.text or "").split("\n")[1:]      # skip the first segment
        if DATE_RE.match(line.strip())
    )
    print(f"soft line breaks  : {soft} paragraph(s) contain internal newlines")
    if soft_dates:
        print(f"  !! {soft_dates} date line(s) hidden inside paragraphs — "
              f"these entries would be MISSED entirely")

    # Did photo hyperlinks survive whatever editors touched this file?
    n_links = sum(
        1 for p in pars for h in p._p.findall(qn("w:hyperlink"))
        if h.get(qn("r:id")) in doc.part.rels
    )
    print(f"hyperlinks        : {n_links}")

    # Entries run from one date line to the next, title included — titles are
    # deliberately NOT classified, so blank-line structure no longer matters.
    # What matters now is entry size, which drives chunking in Phase 2/3.
    bounds = date_idx + [len(pars)]
    sizes = [
        sum(len((pars[j].text or "").strip()) for j in range(a + 1, b))
        for a, b in zip(bounds, bounds[1:])
    ]
    sizes = [s for s in sizes if s]
    if sizes:
        srt = sorted(sizes)
        n = len(srt)
        over = sum(1 for s in srt if s > 2000)
        print(f"entry size (chars): min={srt[0]} median={srt[n // 2]} "
              f"p90={srt[min(int(n * 0.9), n - 1)]} max={srt[-1]}")
        print(f"                    {over}/{n} exceed 2000 chars (need sub-splitting)")
        print(f"total body chars  : {sum(sizes):,}  (~{sum(sizes) // 4:,} tokens)")

    # The journals carry no formatting; report only if that turns out false.
    styles = Counter(_fmt(p) for p in nonempty)
    if len(styles) > 1:
        print(f"styles in use     : {dict(styles.most_common(4))}  "
              f"(expected a single style)")




def _as_docx(path: Path, tmp: Path) -> Path:
    """Legacy .doc -> .docx via macOS textutil, into a scratch dir.

    python-docx cannot read the old binary format at all. textutil ships with
    macOS and handles it, so no extra dependency. The converted copy is
    temporary; originals are never touched.
    """
    if path.suffix.lower() != ".doc":
        return path
    out = tmp / (path.stem + ".docx")
    subprocess.run(
        ["textutil", "-convert", "docx", str(path), "-output", str(out)],
        check=True, capture_output=True,
    )
    return out


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    target = Path(sys.argv[1]).expanduser()

    if target.is_dir():
        files = sorted(
            f for pat in ("*.docx", "*.doc", "*.DOCX", "*.DOC")
            for f in target.rglob(pat)
        )
    else:
        files = [target]
    files = [f for f in files if not f.name.startswith("~$")]  # Word lock files
    if not files:
        sys.exit("no .doc/.docx files found")

    n_legacy = sum(1 for f in files if f.suffix.lower() == ".doc")
    print(f"{len(files)} file(s): {len(files) - n_legacy} .docx, {n_legacy} legacy .doc")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for f in files:
            try:
                probe(_as_docx(f, tmp), display=f.name)
            except subprocess.CalledProcessError as e:
                print(f"\n=== {f.name} ===\n  textutil failed: "
                      f"{e.stderr.decode(errors='replace').strip()[:200]}")
            except Exception as e:                   # keep going across a whole archive
                print(f"\n=== {f.name} ===\n  ERROR: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
