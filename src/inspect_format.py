"""Probe a journal .docx for structure without disclosing its content.

Answers the questions the parser needs answered — are date lines detectable,
are titles visually distinct, how consistent is the formatting — while printing
only counts, style names and character lengths. No journal text is emitted.

    uv run python -m src.inspect_format "<file.docx>"
    uv run python -m src.inspect_format "<dir>"     # every .docx within

Output is safe to share.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

from docx import Document

# "Jun 14", "Jun 4", "Jun 14." — month abbreviation then day, nothing else.
DATE_RE = re.compile(r"^\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})\s*\.?\s*$",
                     re.IGNORECASE)


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


def probe(path: Path) -> None:
    doc = Document(str(path))
    pars = doc.paragraphs

    date_idx = [i for i, p in enumerate(pars) if DATE_RE.match(p.text or "")]
    nonempty = [p for p in pars if (p.text or "").strip()]

    print(f"\n=== {path.name} ===")
    print(f"paragraphs        : {len(pars)}  ({len(nonempty)} non-empty)")
    print(f"date lines found  : {len(date_idx)}")

    if not date_idx:
        print("  !! no 'MMM DD' lines matched — format differs from expectation")
        styles = Counter(_fmt(p) for p in nonempty)
        print(f"  styles present  : {dict(styles.most_common(6))}")
        return

    print(f"date-line format  : {dict(Counter(_fmt(pars[i]) for i in date_idx))}")

    # What follows each date line? Title or straight into the body?
    after: Counter[str] = Counter()
    lengths: list[int] = []
    for i in date_idx:
        j = i + 1
        while j < len(pars) and not (pars[j].text or "").strip():
            j += 1
        if j >= len(pars):
            continue
        after[_fmt(pars[j])] += 1
        lengths.append(len((pars[j].text or "").strip()))

    print(f"line-after format : {dict(after.most_common(6))}")
    if lengths:
        short = sum(1 for n in lengths if n <= 40)
        print(f"line-after length : min={min(lengths)} median={sorted(lengths)[len(lengths)//2]} "
              f"max={max(lengths)}")
        print(f"                    {short}/{len(lengths)} are <=40 chars (title-like)")

    distinct = set(after) - set(Counter(_fmt(pars[i]) for i in date_idx))
    verdict = ("titles look formatting-distinguishable"
               if len(after) > 1 or distinct else
               "no formatting difference — will need text heuristics")
    print(f"verdict           : {verdict}")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    target = Path(sys.argv[1]).expanduser()
    files = sorted(target.rglob("*.docx")) if target.is_dir() else [target]
    files = [f for f in files if not f.name.startswith("~$")]  # Word lock files
    if not files:
        sys.exit("no .docx files found")
    for f in files:
        try:
            probe(f)
        except Exception as e:                       # keep going across a whole archive
            print(f"\n=== {f.name} ===\n  ERROR: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
