# Corpus Inventory

Measured from the real archive with `src/inspect_format.py`, which reports counts and
shapes only. Aggregate figures — individual filenames are deliberately omitted, since
a list of journal names is effectively a travel history and this repo is intended to
go public.

## Scale

| | |
| --- | --- |
| Journal files | 55, all `.docx` (no legacy `.doc`) |
| Entries | **1,749** |
| Body text | **~1.07M tokens** |
| Photo hyperlinks | **11,901** |
| Media files | 15,915 `.jpg`, 153 `.mp4`, 22 `.mov` |
| Archive size | 74 GB |
| Year folders | 18, spanning 2010–2026 |
| Earliest journal | 1994 (predates the media folders) |

**~1.07M tokens settles the long-context question.** The archive does not fit in
`gemma4`'s 256K window — not close. RAG is required on size grounds alone, quite apart
from privacy.

## Date formats found

All 55 files yielded date lines; none failed outright. Variants observed:

```
Apr 21      Apr. 21     April 21     Apr 21.     Apr 21st
Sep. 3      Sep.3       Apr. 21, 2010
```

Adding inline-year and trailing-punctuation support recovered **49 entries** that had
been silently merged into their predecessors (1,700 → 1,749).

The inline year is load-bearing for one file that has **no year in its filename** — the
in-text year is its only date source, and must take precedence over the filename.

## Parser requirements discovered

1. **Split paragraphs on soft line breaks before scanning.** Three files contain a date
   typed after Shift+Enter, which lives inside a paragraph as `\n`. A paragraph-level
   scan cannot see it and the entry vanishes entirely — not merged, *lost*.

2. **A date line starts an entry; the rest of that line is body text.** Two entries have
   the date and the whole entry on one line (78 and 250 words). Since titles are already
   treated as body, this needs no word-count threshold or classification.

3. **Handle preamble.** Text before the first date line is currently dropped. One file
   lost 86,000 characters this way when its first date was late in the document.

4. **Sub-split long entries.** Median entry is ~1,200–3,800 characters, but the largest
   is 16,092. One file holds only 3 entries across 33,631 characters — written in long
   blocks rather than daily. Entry-level chunking alone is not sufficient.

5. **In-text year overrides filename year.**

## Known outliers

- One journal has **no year in its filename** and uses the inline-year date format
  throughout. It is also the only file with a second paragraph style.
- One journal predates the media folders by 16 years, so it has no photos to link.
- **11 of 55 files contain no photo hyperlinks** — the "written in a hurry" case. These
  fall back to folder place/year plus EXIF timestamp matching.
- A top-level folder does not follow the `YYYY/place/` convention and sits beside the
  year folders.
- Windows `Thumbs.db` artifacts and one stray `.zip` inside the media tree; both ignorable.

## Media

Photo links are far more plentiful than expected — 11,901 across 44 files. That is a
dense, explicit text↔image association obtained for free, and it makes the
timestamp-matching fallback a secondary path rather than the primary one.
