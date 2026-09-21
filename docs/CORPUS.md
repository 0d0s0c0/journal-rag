# Corpus Inventory

Measured from the real archive with `src/inspect_format.py`, which reports counts and
shapes only. Aggregate figures — individual filenames are deliberately omitted, since
a list of journal names is effectively a travel history and this repo is intended to
go public.

## Scale

| | |
| --- | --- |
| Journal files | 54, all `.docx` (no legacy `.doc`) |
| Entries | **1,703** |
| Body text | **~1,073,000 tokens** |
| Photo hyperlinks | **11,901** |
| Files with no photo links | 10 of 54 |
| Entries over 2,000 chars | **764 (44%)** |
| Media files | 15,873 `.jpg`, 153 `.mp4`, 22 `.mov` |
| Archive size | ~74 GB |
| Year folders | 17, spanning 2010–2026 |
| Earliest journal | 1994 (predates the media folders) |

Measured after removing two out-of-scope items and with the final date pattern in
place. `raw/` is set read-only (`chmod -R a-w`) — it is the working copy of an
irreplaceable archive, and everything downstream only reads from it.

**~1.07M tokens settles the long-context question.** The archive is roughly 4× too
large for `gemma4`'s 256K window. RAG is required on size grounds alone, quite apart
from privacy.

**44% of entries exceed 2,000 characters**, so entry-level chunking alone is not
enough — sub-splitting is the normal path, not an edge case.

## Date formats found

All 54 files yielded date lines; none failed outright. Variants observed:

```
Apr 21      Apr. 21     April 21     Apr 21.     Apr 21st
Sep. 3      Sep.3       Apr. 21, 2010
```

Adding inline-year and trailing-punctuation support recovered entries that had been
silently merged into their predecessors. Most were in the journal since removed; three
remain in a file that is still present, so the inline year stays supported.

One near-miss remains in the whole archive: a single entry with the date and 78 words
of body on the same line. Handled by the rule that a date line starts an entry and the
rest of that line is body text.

When an inline year is present it takes precedence over the year parsed from the
filename.

## Year rollover is the normal case

The filename year is the year the trip **started**. Travel is frequently late December
into January, so a file named `<place> - 2021` holds December 2021 *and* January 2022
entries. Assigning the filename year to every entry dates those January entries twelve
months early, with nothing to signal it.

The media tree follows the same convention — January photos stay in the previous year's
folder:

```
2020/   2020-12, 2021-01
2021/   2021-12, 2022-01
2022/   2022-12, 2023-01
```

Because both sides share the convention, applying the same rollover rule to journals and
photos keeps them aligned across a New Year, which is what the date-based photo match
depends on.

One anomaly: the 2013 folder contains a photo dated 2015-05. Worth a look, but isolated.

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
- **Three files** contain a date hidden behind a soft line break (see requirement 1).
- **One near-miss** across the archive: date and body text on the same line.
- One file holds only 3 entries across 33,631 characters — written in long blocks
  rather than daily.
- Windows `Thumbs.db` artifacts and one stray `.zip` inside the media tree; both ignorable.

**Removed after the first pass:** a journal that did not belong to the archive, and a
top-level folder outside the `YYYY/place/` convention. Both were flagged by the
inventory as outliers before anything was built on them.

## Media — measured

| | Finding |
| --- | --- |
| Images | 15,873 `.jpg`. **No HEIC**, so no `pillow-heif` needed. |
| `DateTimeOriginal` | Present in essentially every readable photo, every year |
| **GPS latitude/longitude** | **Absent everywhere.** Some files carry a GPS IFD, but it holds only `GPSImgDirection` (compass heading). Location services were off. |
| Cameras | BlackBerry, Huawei, Panasonic, Sony, Apple, Xiaomi, Google, Samsung across 2010–2026 |
| Photo links in text | 11,901 across 44 of 54 files |

**The absence of GPS retired a substantial part of the plan** — reverse geocoding, a
destinations map, and GPS as ground truth for "where was I when." An earlier version of
this document called EXIF GPS "the highest-value piece of this entire project." It does
not exist. It also retires the offline-geocoding privacy concern.

What survives is enough: photo date plus the `<year>/<place>/` folder.

> Spotlight (`mdls`) reported no GPS, then a first Pillow check appeared to find it in
> 2019, then a corrected read of the GPS sub-IFD showed only a compass heading. Only the
> third check was right. Read the actual tag values, not the presence of an IFD pointer.

## Photo references are ~30% of the text

Photo paths appear inline, mid-sentence, in parentheses. Across five sample entries they
are **29% of all characters, 42% in one long entry**. Embedded as-is they dominate the
vector — one entry would embed largely as `vale temple first enclosure root jpg` repeated.

**Strip them from chunk text before embedding; keep them as linked metadata.**

Two path shapes occur, mixed separators: `<year>\<place>\<name>.jpg` and
`<place>\<name>.jpg`. The latter takes its year from the journal filename.

**The filenames are hand-written captions** — `vale temple - guardian lion.jpg`,
`old bridge - looking south.jpg`. Better than a vision model would produce, already
present, free. This substantially undercuts Phase 8.

## Data integrity: 93 corrupt files

15,780 of 15,873 images verify clean. The remaining **93 are all in one 2021 folder**,
where only 2 of 95 files are valid JPEGs:

```
readable    2 files:  header ffd8ff (valid JPEG)
unreadable 93 files:  14 zero-length, 35 all-zero bytes, 44 random headers
```

Zero-length and zero-filled blocks indicate an **interrupted copy or filesystem damage**,
not gradual corruption, and it is confined to that one folder. Recovery means checking
the source drive for intact originals. Unrelated to the RAG work, but it would otherwise
have surfaced months later as broken image paths.
