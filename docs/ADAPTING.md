# Adapting this to your own journals

Phase 1 — turning documents into entries — is specific to one archive's
conventions. Everything after it is not.

```
YOUR ARCHIVE                            REUSABLE AS-IS
──────────────────────────────          ─────────────────────────────
src/convert.py   <place> - <year>       src/chunk.py      chunking
                 Dec→Jan rollover       src/embed.py      vectors + LanceDB
                 inline photo refs      src/search.py     query + filters
                 <year>/<place>/ media  src/evaluate.py   scoring
src/dates.py     "Apr. 21" headers      src/verify_images.py
src/inspect_format.py  .docx probe      scripts/, hooks, privacy design
```

**The dividing line is `entries.jsonl`.** Produce that shape from anything and
the rest of the pipeline works unchanged.

---

## The contract

One JSON object per line:

```json
{
  "id": "ruritania-2019#0042",
  "source_file": "ruritania - 2019.docx",
  "trip": "ruritania",
  "entry_date": "2019-06-14",
  "month_day": "06-14",
  "text": "Zenda\nWe took the boat out early...",
  "photos": [{"path": "2019/zenda/harbour.jpg", "source": "link",
              "resolved": "exact"}],
  "chars": 2103,
  "warnings": ["year_rollover"]
}
```

| Field | Required | Notes |
| --- | --- | --- |
| `id` | yes | Unique and stable. Used by corrections and evaluation, so it must not change between runs for the same entry. |
| `entry_date` | yes | ISO `YYYY-MM-DD`, and a **real calendar date** — `2011-02-29` parsed happily here and raised three phases later. |
| `text` | yes | Plain text, ready to embed. Whatever is *not* prose should already be stripped. |
| `source_file` | yes | Provenance, shown in results. |
| `trip` | no | A label. Filterable, but see the note on granularity below. |
| `photos` | no | `path` relative to the media root. |
| `warnings` | no | Free-form strings recording how this entry was derived. |
| `month_day`, `chars` | no | Convenience. |

Everything else — chunking, embedding, search, evaluation — reads only these.

---

## What to do

**1. Profile before you parse.** Write the equivalent of
`src/inspect_format.py` for your format: a script that reports structure and
counts and prints **no content**, so its output is safe to share and reason
about. Ours found that 42% of one entry was photo filenames and that three
files hid dates inside paragraphs — neither of which was visible by opening a
document and looking at it.

Profile first, then write the parser against what you measured.

**2. Write your converter.** Produce `entries.jsonl`. Expect the hard parts to
be format-specific and undocumented, because they live in habits rather than
specs. Ours were:

- the year lived in the filename, and was the year the trip *started*
- a date typed after Shift+Enter was invisible to a paragraph-level scan
- photo references inline in the prose were a third of all characters

Yours will be different and equally arbitrary.

**3. Give yourself a number to check against.** The profiler said 1,703
entries; the converter produced 1,708 and every extra one was an entry the
profiler had predicted would be lost. Without that number, the converter would
have been "probably fine."

**4. Record decisions, don't make them silently.** The `warnings` field exists
so anything questionable can be grepped for rather than discovered later. A
parser that guesses quietly is worse than one that flags.

**5. Everything downstream then runs:**

```bash
uv run python -m src.chunk        # entries.jsonl -> chunks.jsonl
uv run python -m src.embed        # -> LanceDB index
uv run python -m src.search "..."
uv run python -m src.evaluate
```

---

## Configuration

Copy `config.example.yaml` to `config.yaml` and set `data_root`. Nothing is
hardcoded — paths, models, chunk size and retrieval settings all live there.
`JOURNAL_CONFIG` overrides the file location.

Two settings need care:

**`embedding.model` and `embedding.dimensions` must agree**, and changing
either **invalidates** the index rather than degrading it — vectors from
different models are not comparable. The model name is part of each row's hash
so a mismatch is caught rather than silently returning nonsense. Re-embedding
this archive takes about five minutes, so this is cheap to get wrong.

**`embedding.query_instruction` applies to queries only.** Documents are
embedded bare. Applying it to both degrades the entire index with no error, and
the only symptom is that results are mediocre.

---

## If your journals have no reliable dates

This is the one assumption that cannot be worked around. Dates are load-bearing
for filtering, chunk headers, photo matching, corrections and aggregation.
Without them, most of the design falls over and the project needs rethinking
rather than adapting.

## Granularity of place labels

`trip` comes from the filename here and is usually a country. The finer place
lives in the media folder names, at mixed granularity — city, region or country
depending on how the folders were named. It is a label, not geography. This
archive contains **no GPS at all**, so there is nothing to normalise against;
if yours does, you have an option this one did not.
