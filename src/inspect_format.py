"""Probe journal .doc/.docx files for structure without disclosing content.

Answers the questions the parser needs answered — are date lines detectable,
are titles visually distinct, how consistent is the formatting — while printing
only counts, style names and character lengths. No journal text is emitted.

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

    print(f"date-line format  : {dict(Counter(_fmt(pars[i]) for i in date_idx))}")

    # Soft line breaks (Shift+Enter) live INSIDE a paragraph as '\n'. They look
    # identical to the writer but are invisible if you only iterate paragraphs,
    # so count them before assuming blank paragraphs are the only separator.
    soft = sum(1 for p in pars if "\n" in (p.text or ""))
    print(f"soft line breaks  : {soft} paragraph(s) contain internal newlines"
          f"{'  <-- significant, gaps may be inside paragraphs' if soft else ''}")

    # Empty paragraphs are what the blank-line rule depends on. If an editor
    # (Word or LibreOffice) renders the gap via paragraph SPACING instead, the
    # visual layout is unchanged but the empty paragraphs are gone — and the
    # rule silently fails. Report both so the cause is visible.
    n_empty = len(pars) - len(nonempty)
    spaced = sum(
        1 for p in nonempty
        if (p.paragraph_format.space_after is not None
            and p.paragraph_format.space_after.pt > 0)
        or (p.paragraph_format.space_before is not None
            and p.paragraph_format.space_before.pt > 0)
    )
    print(f"empty paragraphs  : {n_empty}")
    print(f"paragraph spacing : {spaced}/{len(nonempty)} non-empty paras carry "
          f"space before/after")
    if n_empty == 0 and spaced:
        print("  !! gaps are SPACING, not empty paragraphs — blank-line rule will not work")

    # Did photo hyperlinks survive whatever editors touched this file?
    n_links = sum(
        1 for p in pars for h in p._p.findall(qn("w:hyperlink"))
        if h.get(qn("r:id")) in doc.part.rels
    )
    print(f"hyperlinks        : {n_links}")

    # The shape of each entry: how many blank paragraphs follow the date, how
    # long the next non-empty line is, then the same again. If a double gap
    # delimits the header, that pattern will dominate.
    shapes: Counter[str] = Counter()
    after_fmt: Counter[str] = Counter()

    for i in date_idx:
        j = i + 1
        gap1 = 0
        while j < len(pars) and not (pars[j].text or "").strip():
            gap1 += 1
            j += 1
        if j >= len(pars):
            continue
        len1 = len((pars[j].text or "").strip())
        after_fmt[_fmt(pars[j])] += 1

        k = j + 1
        gap2 = 0
        while k < len(pars) and not (pars[k].text or "").strip():
            gap2 += 1
            k += 1
        len2 = len((pars[k].text or "").strip()) if k < len(pars) else 0

        shapes[f"date +{gap1}blank -> {_bucket(len1)} +{gap2}blank -> {_bucket(len2)}"] += 1

    print(f"line-after format : {dict(after_fmt.most_common(4))}")
    print("entry shapes (most common first):")
    for shape, n in shapes.most_common(6):
        print(f"   {n:4d}x  {shape}")

    distinct = set(after_fmt) - set(Counter(_fmt(pars[i]) for i in date_idx))
    print("verdict           : "
          + ("titles are formatting-distinguishable"
             if distinct else
             "no formatting difference — rely on blank-line structure"))


def _bucket(n: int) -> str:
    """Length as a coarse bucket, so nothing about the text itself is revealed."""
    if n == 0:
        return "(end)"
    if n <= 40:
        return f"short({n})"
    if n <= 120:
        return "medium"
    return "long"


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
