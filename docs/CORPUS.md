# Corpus Inventory

Measured from the real archive with `src/inspect_format.py`, which reports counts and
shapes only. Aggregate figures — individual filenames are deliberately omitted, since
a list of journal names is effectively a travel history and this repo is intended to
go public.

## Scale

| | |
| --- | --- |
| Journal files | 54, all `.docx` (no legacy `.doc`) |
| Entries | **~1,703** |
| Body text | **~1.05M tokens** |
| Photo hyperlinks | **11,901** |
| Media files | ~15,900 `.jpg`, 153 `.mp4`, 22 `.mov` |
| Archive size | ~74 GB |
| Year folders | 17, spanning 2010–2026 |
| Earliest journal | 1994 (predates the media folders) |

> Two items were removed from the archive after the first measurement: a journal that
> did not belong, and a top-level folder outside the `YYYY/place/` convention. The
> figures above are the original measurements less those two; re-run
> `src/inspect_format.py` to confirm exactly.

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
been silently merged into their predecessors. Most of those were in the journal since
removed from the archive; three remain in a file that is still present, so the inline
year stays supported.

When an inline year is present it takes precedence over the year parsed from the
filename.

## Parser requirements discovered

1. **Split paragraphs on soft line breaks before scanning.** Three files contain a date
   typed after Shift+Enter, which lives inside a paragraph as `\n`. A paragraph-level
   scan cannot see it and the entry vanishes entirely — not merged, *lost*.

2. **A date line starts an entry; the rest of that line is body text.** Two entries have
   the date and the whole entry on one line (78 and 250 words). Since titles are already
   treated as body, this needs no word-count threshold or classification.

3. **Handle preamble.** Text before the first date line is currently dropped. This was
   observed on a file since removed, but the defect is real and would silently lose the
   opening of any file whose first date appears late.

4. **Sub-split long entries.** Median entry is ~1,200–3,800 characters, but the largest
   is 16,092. One file holds only 3 entries across 33,631 characters — written in long
   blocks rather than daily. Entry-level chunking alone is not sufficient.

5. **In-text year overrides filename year.**

## Known outliers

- One journal predates the media folders by 16 years, so it has no photos to link.
- **10 of 54 files contain no photo hyperlinks** — the "written in a hurry" case. These
  fall back to folder place/year plus EXIF timestamp matching.
- Three files contain a date hidden behind a soft line break (see requirement 1).
- One file holds only 3 entries across 33,631 characters — written in long blocks
  rather than daily.
- Windows `Thumbs.db` artifacts and one stray `.zip` inside the media tree; both ignorable.

**Removed after the first pass:** a journal that did not belong to the archive, and a
top-level folder outside the `YYYY/place/` convention. Both were flagged by the
inventory as outliers before anything was built on them.

## Media

Photo links are far more plentiful than expected — 11,901 across 44 files. That is a
dense, explicit text↔image association obtained for free, and it makes the
timestamp-matching fallback a secondary path rather than the primary one.
