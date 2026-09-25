# Journal RAG

Ask natural-language questions about years of personal travel journals — *"Where did I
travel in 2019?"*, *"What were my favourite meals and where did I have them?"*,
*"What were the best places I snorkelled?"*, *"Which museums did I actually like?"* —
answered from the actual text, running entirely offline on a local LLM.

A learning project, built one phase at a time. The journals themselves are private and
live outside this repository; only code is tracked here.

**Status:** Phases 0–3 complete — 54 documents parsed into 1,708 dated entries with
14,305 photos attached, chunked into 2,919 passages, embedded into a local vector index,
and searchable from the command line. Phases 4–8 (generation, hybrid retrieval, the
experience index, evaluation, interface) are planned and described below.

Much of what is written here is a record of being wrong: assumptions about the date
format, the photo metadata, and what semantic search can do were each corrected by
measuring the real archive. Those corrections are kept rather than tidied away —
[`docs/CORPUS.md`](docs/CORPUS.md) has the measurements and
[`docs/SETUP.md`](docs/SETUP.md) has the failures.

**Using this with your own journals?** Phase 1 (documents → entries) is specific to one
archive's conventions; everything after it is not. See
[`docs/ADAPTING.md`](docs/ADAPTING.md) for the `entries.jsonl` contract and what
writing your own converter involves.

---

## Why RAG, not fine-tuning

The instinct is to "train a model on my journals." That is almost always the wrong tool:

|                        | Fine-tuning            | RAG (this project)              |
| ---------------------- | ---------------------- | ------------------------------- |
| Teaches the model      | *style and behavior*   | *facts and content*             |
| Recall of specifics    | Fuzzy, hallucination-prone | Quotes the actual entry     |
| Adding a new journal   | Retrain                | Drop in a file, re-index        |
| Can cite its source    | No                     | Yes — "from your 2019-04-11 entry" |
| Cost on a laptop       | Hours, finicky         | Minutes                         |

Fine-tuning would make a model *write like* the journals. RAG makes a model *answer
from* them.

## The two-query-types insight

This shapes the whole design. The motivating questions are not one problem but two:

1. **"Tell me about the diving"** — *semantic*. Vector search excels here. The journal
   says "the uni in Vladero ruined me for all other sea urchin" and never uses the word
   "standout"; it describes a reef without writing "snorkelling." Embeddings catch that;
   keyword search does not.

2. **"Where did I travel in 2019?"** — *aggregation over a filtered set*. Vector search
   is **bad** at this. It returns the top-k most similar chunks, so if there were nine
   destinations in 2019 and k=5, the answer is confidently incomplete.

Most RAG tutorials build only the first kind, which is why they demo beautifully and
disappoint on a real archive. Phases 1–4 build the semantic path and the fundamentals.
**Phase 5** — metadata filtering, keyword search fused with vectors, and an offline
extraction pass into an **experience index** — is what makes the second kind work, and
what extends the whole system beyond food to activities, sites, museums, lodging,
transport, people and wildlife.

There is a sharper edge to (1) than it first appears, measured during setup:
**embeddings identify subject matter, but barely separate praise from complaint.**
Against the query "outstanding food I had", "we ate a forgettable sandwich at the
airport" and "the uni in Vladero ruined me for all other sea urchin" score within
0.006 of each other — noise, and their order flips if a single word changes. A
genuinely irrelevant passage sits far below.

So the semantic path narrows to *the right subject* and no further. Whether a meal, a
reef or a museum was actually **good** is invisible to it — that judgment has to come
from the LLM reading the entry, or from the sentiment recorded during Phase 5
extraction. See [`docs/SETUP.md`](docs/SETUP.md) for the numbers.

## Architecture

```
.doc/.docx  ──►  clean text  ──►  entry-level chunks + metadata
                                          │
   photos  ──►  EXIF date + folder place ─┤
                 (no GPS in this archive)  │
                        ┌─────────────────┼─────────────────┐
                        ▼                 ▼                 ▼
                  vector index      keyword index   experience index
                   (semantic)          (BM25)        (food, activities,
                        │                 │           sites, museums,
                        │                 │           people, places,
                        │                 │           sentiment, dates)
                        └────────┬────────┘                 │
                                 ▼                          │
                          hybrid retrieval  ◄────────────────┘
                                 │
                                 ▼
                          local LLM (Ollama)
                                 │
                                 ▼
          answer + citations to entries + photo paths
```

## Photos

Journals come with photographs, and they are not decoration — they carry data the
prose does not.

### What the photos actually contain — measured, not assumed

| | Finding |
| --- | --- |
| `DateTimeOriginal` | Present in essentially every readable photo, every year |
| **GPS latitude/longitude** | **Absent everywhere.** The GPS IFD exists in some files but holds only `GPSImgDirection` — a compass heading. Location services were off. |
| Format | All `.jpg`. No HEIC, so no `pillow-heif` needed. |
| Integrity | All 15,854 verify readable. 93 corrupt files were found in one 2021 folder and 74 recovered from the source drive — see [`docs/CORPUS.md`](docs/CORPUS.md). |

The absence of GPS retires a plan that appeared here through several revisions: reverse
geocoding, a destinations map, and GPS as ground truth for "where was I when." None of
it is possible. It also retires the offline-geocoding privacy concern, which is now moot.

**What survives is sufficient.** Photo date plus the `<year>/<place>/` folder gives when
and where, which is all the linking needs.

### Association: two paths, both simple

| Case | Method |
| --- | --- |
| Journal links its photos (44 of 54 files, 11,901 links) | The inline hyperlink gives the target path *and* the paragraph it sits in — exact placement within the entry |
| No links (10 of 54 files, mostly recent) | Match photo `DateTimeOriginal` to the entry with that date |

Deliberately nothing more elaborate. Date-to-date matching is enough.

Link targets are parsed rather than resolved by Word, so stale Windows paths re-root
onto the local archive by keeping the trailing `<year>/<place>/<filename>`. Two shapes
occur: `<year>\<place>\<name>.jpg` and `<place>\<name>.jpg` — the latter takes its year
from the journal's filename.

> **Camera clocks.** A camera left on home time during a foreign trip shifts photos to
> the wrong day. Detectable by checking whether a trip's photo dates align with its
> entry dates, and correctable per-trip as a fixed offset. Worth checking before
> trusting the date match.

### The filenames are already captions

```
vale temple - guardian lion.jpg          phare syldavian circus - acrobatics.jpg
vale temple - first enclosure root.jpg     old bridge - looking south.jpg
```

Written by hand, at the time, by someone who was there. A vision model would produce
"ancient stone temple with tree roots"; these are better, already exist, and cost
nothing. **This substantially undercuts the case for Phase 8 vision captioning** — which
drops from "enhancement" to "probably unnecessary."

### Photo references are ~30% of the text, and must be stripped before embedding

Photo paths appear inline, mid-sentence, in parentheses. Measured across five sample
entries they are **29% of all characters — 42% in one long entry.**

Embedded as-is, such an entry's vector is dominated by `vale temple first enclosure root
jpg` repeated eleven times rather than by what was written about tree roots and rain.
Semantic search over it would be badly degraded with nothing to indicate why.

**So: strip photo references from chunk text before embedding; retain them as linked
metadata.** This belongs in Phase 1, not as a Phase 3 discovery.

## Setup

```bash
cp config.example.yaml config.yaml     # then edit data_root
./scripts/setup-repo.sh                # nbstripout + pre-commit hook
uv sync
./scripts/ollama-serve.sh              # in its own terminal

uv run python -m src.convert           # documents  -> entries.jsonl
uv run python -m src.chunk             # entries    -> chunks.jsonl
uv run python -m src.embed             # chunks     -> LanceDB index
uv run python -m src.search "..."      # retrieval
uv run python -m src.evaluate          # score it
```

Paths, models, chunk size and retrieval settings all live in `config.yaml`;
nothing is hardcoded.

## Stack

| Piece | Choice | Why |
| --- | --- | --- |
| Model server | Ollama | Simplest local serving on Apple Silicon; Metal-accelerated; HTTP API mirrors hosted APIs |
| Python env | uv, pinned Python | Pins a version independent of the system Python, avoiding ML wheel gaps on the newest releases |
| Vector store | LanceDB / Chroma | Embedded — a folder on disk, no server — with metadata filtering |
| Legacy `.doc` | macOS `textutil` | Built in; handles the old binary format with no extra tooling |

Target machine for these notes: Apple M1 Max, 32 GB RAM.

---

## Privacy design

Everything runs offline: models served locally by Ollama, vector DB on disk, no API
calls, no telemetry. To be verified rather than assumed — by disabling library analytics
explicitly and by pulling the network mid-query in Phase 4.

**No journal content is committed to this repo, ever.** It is private while under
construction and intended to go public — so the rule holds from the first commit, not
from whenever the switch is flipped.

### Layout

```
playground/
  journal/                 ← this repo, public. CODE ONLY.
  journal-data/            ← sibling, never tracked by git
    raw/    text/    index/    facts.db    eval/
```

The primary defense is not `.gitignore` — it is that git cannot see outside its own
directory. (`playground/` is deliberately not itself a repo; if it were, a "sibling"
would be *inside* one and this would silently fail.) `.gitignore` is a backstop.

### The non-obvious leak vectors

Derived artifacts don't look like journals but contain the text verbatim:

- **Vector index** — LanceDB/Chroma store the original chunk text beside each embedding.
  The index is a copy of the journals.
- **Notebook outputs** — the sneakiest. Retrieval tests print journal excerpts, and
  `.ipynb` saves those outputs *inside the tracked file*. `.gitignore` can't help;
  `nbstripout` as a git filter can.
- **`facts.db`** — extracted places, dishes, restaurants, people.
- **Eval CSVs** — real questions paired with real answers.
- **Raw embeddings** — embedding-inversion research shows partial source reconstruction
  is possible. Don't publish the vectors either.

### Rules

- `.gitignore` does **not** untrack an already-tracked file. Anything that slips in must
  be removed from history before pushing; if already pushed, treat it as disclosed.
- Read `git status` before every commit.
- GitHub push protection scans for credentials, not personal writing. It will not catch
  this.

---

## Steps

Exact commands, rationale, and the problems hit along the way are recorded in
[`docs/SETUP.md`](docs/SETUP.md) — written to be re-runnable on a fresh machine.
Measurements from the real archive, and the parser requirements they revealed, are in
[`docs/CORPUS.md`](docs/CORPUS.md).

### Phase 0 — Environment

- [x] `git init`; commit `.gitignore` **first**, before any data is nearby
- [x] Create `journal-data/` as a sibling; confirm `git status` stays clean
- [x] Install `uv` and `ollama`
- [x] Choose and pull a chat model and an embedding model
      (`gemma4:12b`, `qwen3-embedding:0.6b`)
- [x] Smoke test: one chat completion, one embedding; check throughput
- [x] Python environment via `uv` (3.14 — verified by install, not assumed)
- [x] Install `nbstripout`, register as a git filter (before the first notebook exists)
- [x] Add pre-commit hook rejecting journal file types
- [ ] Turn off editor/library telemetry

> **After cloning, run `./scripts/setup-repo.sh`.** The nbstripout filter and the
> hooks path live in `.git/config`, which is not committed — a fresh clone has no
> protection until you run it.

### Phase 1 — Ingestion

#### Source layout

```
<archive root>/
    Ruritania - 2019.docx          ← journals live at the root
    Syldavia - 2018.doc                "<place> - <YYYY>.doc(x)"
    ...
    2019/                        ← media, foldered by year
        Strelsau/                       then by place
            IMG_1234.jpg
            clip.mov
        Zenda/
    2018/
        Klow/
```

This layout does a lot of work for us:

**The year is in the filename** — but it is the year the trip *started*. Travel is often
late December into January, so `<place> - 2021.docx` contains both 2021 and 2022
entries. The base year is known rather than guessed, but rollover detection is required,
not optional.

**The folder tree maps media → place → year with no inference at all.**
`2019/Strelsau/IMG_1234.jpg` gives place and year directly. Since the archive contains
**no GPS whatsoever**, this is the only location data there is.

**Place folder names are labels, not geography.** Granularity is inconsistent — a folder
may be a city (`Strelsau`), a region (`Northmark`), or a country. So "where did I travel
in 2019?" answered from folder names returns a mix of the three. There is no GPS to
normalise against; the honest options are to accept the mix, or to resolve places during
Phase 5 extraction using what the text itself says.

| Source | Gives | Trust |
| --- | --- | --- |
| Folder name | The trip label, mixed granularity | The only location metadata available |
| Text mentions | Places written about, often precise | Richer, needs extraction |
| EXIF GPS | — | **Does not exist in this archive** |

> **Preserve the directory structure when transferring.** Copy the whole archive in
> one operation from the root rather than moving journals and media separately.
>
> Less critical than it first appears: `src/docx_links.py` parses link targets itself
> rather than asking Word to resolve them, keeping the trailing
> `<year>/<place>/<filename>`. So even a stale Windows absolute path
> (`file:///C:/Users/me/Journals/2019/Zenda%20Bay/IMG_9876.jpg`) re-roots onto the
> local archive correctly. Preserving the tree keeps that mapping aligned; it is not
> make-or-break.

#### Photo association

Older journals link their photos inline; newer ones (written in a hurry) do not, but
their photos still exist in the folder tree and should be included.

| Case | Method |
| --- | --- |
| Journal links the photo | Extract the relative path from the `.docx`, with its paragraph position |
| No link | Match photo `DateTimeOriginal` to the entry with that date |

Nothing more elaborate — date-to-date is enough.

**Videos** (`.mov`, `.mp4`) carry creation dates and attach the same way. Captioning
them would mean frame sampling; out of scope.

#### Steps

- [ ] Get the files onto this machine **without routing them through cloud storage.**
      Dropbox/Drive/iCloud is the convenient path and would upload the entire archive
      to a third party — precisely what this project exists to avoid. Use a USB drive,
      a direct Finder network share, or AirDrop between your own devices.
- [ ] Copy the whole tree in one operation, preserving structure (see warning above)
- [x] Inventory: 54 journals, all `.docx`, 1,703 entries, ~1.07M tokens,
      11,901 photo links — see [`docs/CORPUS.md`](docs/CORPUS.md)
- [x] Run `src/inspect_format.py` over the archive — every file yielded date lines;
      extending the pattern recovered 49 silently-merged entries
- [x] **Count total tokens** — ~1.07M, roughly 4x a 256K context window.
      RAG is required on size grounds, not only privacy.
- [x] Inventory media: 15,873 `.jpg` (no HEIC), dates present, **no GPS anywhere**
- [ ] **Strip inline photo references from chunk text** — they are ~30% of characters
      and would dominate the embeddings
- [x] Determine link type: **external hyperlinks** (Word Insert → Link), extracted by
      `src/docx_links.py` — verified against a synthetic journal
- [ ] Convert `.docx` via python-docx (paragraphs only — the journals carry no
      formatting, so there is no structure to preserve beyond paragraph breaks)
- [ ] Extract embedded images and link targets, recording position in the document
- [ ] Convert legacy `.doc` via `textutil`
- [ ] Quality pass: encodings, stray artifacts, entries the date regex missed
- [x] Write a manifest; flag anything that converted badly
- [x] **Rebuild entries for trips whose journal is lost** — `src/reconstruct.py`.
      Six media folders had no journal at all, but where the photographs were
      *renamed by hand* a journal once existed: the renaming was done so the prose
      could reference them. The dates survive in EXIF, the place in the folder, and
      the subjects in the filenames — captions written at the time. 33 entries
      covering 920 photographs. Kept in a separate file, flagged `reconstructed`,
      and every search result says so. These are captions, never prose.
- [x] **Date corrections without touching the source.** The journals contain a few
      mistyped date headers — a month slip while the day continues the sequence.
      `journal-data/corrections.txt` holds `<entry-id>: YYYY-MM-DD` overrides applied
      at conversion time, so originals stay read-only and unmodified, no editor
      round-trip risks the hyperlinks, and every fix is reviewable and reversible.
      The original value is preserved in the entry's warnings.

*The least glamorous, highest-leverage phase. Garbage here poisons everything downstream.*

### Phase 2 — Chunking and metadata

The source format is:

```
Apr. 21                   ← month + day on its own line (see variants below)
Zenda               ← optional title, own line
We took the boat out early. Two days ago in Strelsau I had
the best noodle soup of my life at a place near the station...
```

Date lines are the **only** structure in the archive — there is no formatting at all —
so the recogniser in `src/dates.py` is load-bearing and is shared by the probe and the
parser rather than duplicated. Observed variants, all accepted:

```
Apr 21      Apr. 21     April 21     Apr 21.     Apr 21st     Sept. 3
```

The period after the *month* is the common form and was missing from the first pattern
— it would have matched almost nothing. `tests/test_dates.py` pins both directions:
these forms must parse, and prose beginning with a month prefix (`Mayonnaise was
involved`, `Marched up the hill`, `Decided to stay in`) must not.

A three-letter month and day, an optional title on the following line, then the entry.
Four consequences, in increasing order of difficulty.

**0. The title is not distinguishable by formatting or by length.** Titles are plain
text, identical in style to the body. Length heuristics fool easily — a one-line entry
such as `"Rain all day."` is shorter than most titles.

**The signal is blank-line structure.** The date is *always* followed by two carriage
returns, so that gap carries no information. The discriminator is the gap after the
**next** line:

```
Jun 14                       Jun 15
                             
                             
Zenda   ← title        Rain all day…  ← body starts directly
                             
                             
We took the boat…
```

| Gap after the line following the date | Meaning |
| --- | --- |
| 2 blanks | that line was a **title** |
| fewer | that line was the **body** |

**Body paragraphs also use two carriage returns**, which means this structural rule
does *not* work: `date / 2CR / X / 2CR / Y` is genuinely ambiguous, and X could be a
title or a first paragraph. No layout rule can separate them.

**The journals carry no formatting at all** — no bold, no headings, no styles. So
formatting was never going to be a usable signal, and the archive's entire structure
reduces to one rule: **a date line starts a new entry; everything else is text.**

**Decision: don't classify at all.** The title is treated as part of the entry.

An entry is everything between one date line and the next, title included. Nothing is
lost — the title text is still in the chunk, still embedded, still searchable. What is
given up is a separate `entry_title` metadata field, and citations use date plus the
filename's trip label instead (`2019-06-14, Ruritania`), which cannot be wrong.

This removes a classifier, fuzzy place matching, a confidence threshold and a review
list from the design. The blank-line structure stops mattering entirely.


> **Soft line breaks are a trap here.** Shift+Enter produces a line break *inside* a
> paragraph (`\n` in the text) rather than a new paragraph. Visually identical, but
> invisible to code that only iterates paragraphs. The probe counts these separately.

**1. No year in the header.** `Jun 14` is ambiguous on its own — but the filename
carries it (`Ruritania - 2019.docx`), so this is largely solved. File-level metadata is
load-bearing: any file whose year cannot be parsed from its name becomes a manual
review item rather than a guess.

**2. Year rollover — common, not an edge case.** The filename year is the year the trip
*started*. Travel is frequently late December into January, so a file named
`<place> - 2021` holds both December 2021 and January 2022 entries. Assigning the
filename year to every entry would date those January entries **twelve months early**,
silently.

Rule: process entries in document order and increment the year whenever the month moves
backwards (`Dec` → `Jan`).

Confirmed in the media tree, where the same convention holds — January photos stay in
the previous year's folder:

```
2020/   photos from 2020-12 and 2021-01
2021/   photos from 2021-12 and 2022-01
2022/   photos from 2022-12 and 2023-01
```

Applying the same rollover rule to journals and photos keeps both sides aligned, which
is what makes the date-based photo match work across a New Year.

**3. Relative dates — the one that breaks the naive model.** "Two days ago in Strelsau I
had the best noodle soup of my life" means the *event* happened on Jun 12, while the *text*
lives in the Jun 14 entry.

A chunk therefore cannot have a single date. Ask "what did I eat on June 12?" and a
system that only knows entry dates searches the Jun 12 entry, finds no noodle soup, and reports
nothing — while the answer sits two entries later.

**Two date fields, not one:**

| Field | Meaning | Source |
| --- | --- | --- |
| `entry_date` | when it was written | the `Jun 14` header |
| `event_date` | when it actually happened | resolved from the text |

This is a further argument for the Phase 5 extraction pass: resolving "two days ago"
against a known entry date is exactly what an LLM does well and what regex does badly.
The extraction prompt must ask for the event date explicitly.

Photo EXIF corroborates: a photo of noodle soup timestamped Jun 12 confirms the resolution.

- [x] Chunk by journal **entry**, not character count — `src/chunk.py`.
      1,708 entries -> **2,919 chunks** (1.71 per entry); 59% stay whole.
      Long entries split on paragraph boundaries, which in these journals fall
      between distinct experiences; sentence splitting with one-sentence overlap
      is the last resort for a single oversized paragraph.
      **99% of chunks begin at a sentence boundary.**
- [ ] Parse the `MMM DD` line; take the year from the filename (`<place> - <YYYY>`)
- [ ] Treat everything between one date line and the next as the entry, title included
      — no title classification (see above)
- [ ] **Detect year rollover** — the filename year is the trip's *start* year, and
      Dec→Jan travel is common. Increment the year when the month moves backwards.
- [ ] Flag files whose year cannot be parsed from the name — review, don't guess
- [ ] Take the trip label from the filename and media folder names — treat as a label,
      not geography; granularity is inconsistent (city / region / country)
- [ ] Check each trip for a camera-clock offset before trusting the date match
- [ ] Read photo `DateTimeOriginal`; match to the entry with that date. Place comes
      from the `<year>/<place>/` folder — there is no GPS to geocode.
- [ ] Record `entry_date` for every chunk; leave `event_date` resolution to Phase 5
- [ ] Attach metadata: `entry_date`, `year`, `month`, `source_file`, `trip`
- [x] Prepend context to chunk text so each is interpretable alone —
      every chunk carries `<date>, <trip> — `. A fragment reading "Everything
      felt like it was cooked in microwave" is unfindable on its own; stamped
      with its date and trip it is searchable by place and time.

### Phase 3 — Index and retrieve (no LLM yet)

- [ ] Embed all chunks into a local vector store
- [ ] Build a retrieval CLI printing top-k chunks with scores and dates
- [ ] Write 15–20 real questions with known answers
- [ ] **Evaluate retrieval on its own, before adding a model** — if the right entry
      isn't in the top 5, no LLM can rescue the answer

### Phase 4 — First working RAG

- [ ] Assemble the prompt: question + retrieved chunks + instructions
- [ ] Ground it hard — answer only from provided entries, say "not in the journals"
      rather than guess
- [ ] Cite the source entry date for every claim
- [ ] Verify offline operation with the network disconnected
- [ ] Run the real questions; expect the "where in 2019" class to underperform

### Phase 5 — Hybrid retrieval and the experience index

This is where the interesting questions become answerable. Not just *"where did I travel
in 2019"*, but:

> *"What were some of my favourite meals and where did I have them?"*
> *"What were some of my favourite snorkelling places?"*
> *"Which museums did I actually like?"*
> *"Who did I meet in Ruritania?"*
> *"What were the highlights of 2019?"*

Vector search cannot answer these, for two independent reasons:

**Judgment.** *"Favourite"* is valence, and embeddings barely encode it — measured at
0.006 separation between praise and complaint, with the order flipping on a single word
change. Retrieval surfaces meal-related entries, not *good*-meal entries.

**Coverage.** These are questions about 1,703 entries across 16 years. Top-k retrieval
sees perhaps 15. The model then answers fluently from 1% of the archive with nothing
signalling how little it saw — a confidently incomplete answer, which is worse than a
refusal.

The extraction pass fixes both: read **every** entry once, offline, and write structured
rows. The question then queries 1,703 facts instead of guessing from 15 chunks.

#### Schema: one `experience` table, not one per category

Travel is food *and* activities, sites, museums, lodging, transport, people, wildlife,
mishaps. A single table with a `type` column beats parallel tables — one extraction
prompt to maintain, and cross-category questions ("highlights of 2019") need no union.

| Column | Notes |
| --- | --- |
| `type` | **Controlled vocabulary** — see below. Free text here and the LLM invents fifty near-synonyms, breaking every query. |
| `name` | The thing itself — a dish, a reef, a museum, a person |
| `place` | Where it happened, as written |
| `setting` | `home` \| `out` \| `transit` — see below |
| `sentiment` | Ordinal −2…+2, not free text. This is what makes "favourite" queryable. |
| `evidence` | The verbatim phrase that justified the sentiment |
| `entry_date` / `event_date` | When written vs. when it happened |
| `source_file`, `trip`, `photo_paths` | Provenance and linked images |

**Types:** `food`, `drink`, `activity`, `site`, `museum`, `lodging`, `transport`,
`person`, `wildlife`, `purchase`, `mishap`, `other`.

#### Why `setting` exists

The archive is not all holidays. Long stays — a two-month spell abroad — produce entries
about working, cleaning, grocery shopping and cooking dinner at home:

> *Spent the day working on a friend's website and cleaning the kitchen… walked out to
> the local station to buy hotpot ingredients… Pretty tasty.*

Without `setting`, that home-cooked noodle dish lands in *"what were my favourite meals
and where did I have them?"* next to restaurant meals from a trip, and the answer
becomes a muddle. With it, that query filters to `setting=out` while *"what did I cook
while I was in Syldavia?"* — a good question these entries can answer — filters to
`setting=home`.

The data is preserved either way; the field is what keeps the two separable. Adding it
later would mean re-running the whole extraction.

#### One row per named thing, not one per event

A single dinner can carry six separate judgements:

> *"The scallops was ok, the beef (thin and tough) and fries with mushroom sauce was
> just that… The stew pork… was bland. The fondant too sweet and the crème brulee had
> the consistency of custard tart."*

Extract a row for each named item rather than one row for "dinner". It makes *"which
dishes did I dislike?"* answerable, and each row carries its own `evidence`.

Sentiment also **diverges within one venue**. That restaurant's food is −2, but *"The
old man was nice, explaining the items on the menu"* is a separate `person` row at +1.
One table with independent rows represents that correctly; a single per-venue rating
could not.

#### Give the model the region — it resolves places it otherwise gets wrong

Every chunk already carries a synthetic header, `2016-09-19, wyoming — `, stamped from
the journal's filename during chunking. It was added so a retrieved fragment would be
self-contained; it turns out to do a second job.

Measured with `gemma4:12b`:

```
"black canyon"
  bare     -> "a section of the Grand Canyon in Arizona"      WRONG
  +region  -> "in the Gunnison National Forest in Colorado"   right

"old trail town"
  bare     -> "not a recognized geographical location"        gives up
  +region  -> "located in Cody, Wyoming"                      right
```

A place name alone is ambiguous worldwide. With the trip label the model resolves it,
and the label is usefully sized: foreign trips give a country, US trips give a state —
narrow enough to disambiguate, broad enough to be reliably correct.

**So the extraction prompt must include the header, not just the body.** Without it,
"black canyon - gunnison river" gets filed under Arizona, and the facts table is
confidently wrong in a way nothing downstream would catch.

**But the model is recalling, not looking up.** It said National *Forest* where the
answer is National *Park*. Close enough to disambiguate, not close enough to cite —
which is exactly why `evidence` records what the journal actually said rather than what
the model inferred around it.

#### Comparisons are not visits

The journals compare places to other places from other trips:

> *"reminiscent of **Elbonia**"* · *"like a miniature version of **Vespugia**"* ·
> *"reminiscent of **Borduria** but cleaner"*

Read naively, an extractor records Elbonia, Vespugia and Borduria as places visited that
day — and *"where did I travel in 2019?"* returns three countries that were never
visited. That is worse than a missing row: a **confidently wrong fact**, indistinguishable
in the index from a true one.

The prompt must state explicitly that only places actually visited are recorded, and
that a place named as a comparison, a memory, or a plan is not a visit. Worth testing
rather than assuming — this is the kind of instruction models follow only partially.

#### Sentiment is understated, and must be calibrated

Two genuine approvals from the same archive:

> *"A very satisfying meal, liberal with the salt, just the way I liked it."*
> *"Pretty tasty."*

The same feeling, wildly different intensity of language. A 12B model will likely score
the second as neutral, which quietly drops the mundane-but-liked things out of
"favourites." **The extraction prompt needs calibration examples taken from the real
journals**, not generic ones — few-shot pairs showing that "pretty tasty" is positive in
this writer's register.

The negative end is more explicit and needs less help — *"terrible"*, *"bland"*,
*"underwhelming"*, *"thin and tough"* — so anchor the scale with a real example from
each extreme and let the middle calibrate against them.

`evidence` earns its place: it lets an answer say *"the snorkelling at X — you wrote
'best visibility I've ever seen'"* rather than asserting a preference you cannot check.
It also makes wrong extractions visible instead of silently authoritative.

#### What each question becomes

| Question | Query |
| --- | --- |
| favourite meals and where | `type=food`, `setting=out`, `sentiment>=1`, return `name, place` |
| what did I cook in Syldavia | `type=food`, `setting=home`, `trip~syldavia` |
| favourite snorkelling places | `type=activity`, `name~snorkel`, `sentiment>=1` |
| museums I liked | `type=museum`, `sentiment>=1` |
| people met in Ruritania | `type=person`, `place~Ruritania` |
| highlights of 2019 | any type, `sentiment=2`, `year=2019` |
| where did I travel in 2019 | distinct `place` where `year=2019` |

Every one is exhaustive over the archive and cheap at query time. The extraction is the
expensive part, and it runs once.

- [ ] Parse year/date ranges from questions; filter *before* semantic ranking
- [ ] Add BM25 keyword search; fuse rankings with vectors
- [ ] Offline extraction pass over all 1,703 entries into the `experience` table
- [ ] Include the chunk header (date + trip label) in the extraction prompt — without
      the region, place names resolve to the wrong continent
- [ ] Pin the `type` and `setting` vocabularies in the prompt; reject anything outside them
- [ ] Calibrate sentiment with few-shot examples drawn from the real journals —
      "pretty tasty" must not score neutral
- [ ] Rule: record only places actually visited. Comparisons, memories and plans are
      not visits — and **test it**, rather than trusting the instruction
- [ ] One row per named item, not per event; allow sentiment to diverge within a venue
- [ ] Record an `extraction_version` so the pass can be re-run with a better prompt
      later without ambiguity about which rows came from where
- [ ] Spot-check a sample against memory — a 12B model will miss some enthusiasm and
      over-read other passages; Phase 6's eval set is how that gets measured
- [ ] **Resolve relative dates** ("two days ago", "last Tuesday", "the night before")
      against the entry date, and store the result as `event_date`. Facts are indexed
      by when they *happened*, not by when they were written about.
- [ ] Cross-check resolved dates against photo EXIF where a photo exists
- [ ] Load photo dates and folder places into the experience index
- [ ] Route by question type: aggregation → facts table, open recall → hybrid search
- [ ] Return photo paths alongside answers, linked by document position or timestamp

*Where this stops being a tutorial and becomes useful.*

### Phase 6 — Evaluation

- [ ] Grow the question set to 30–50, spanning both query types
- [ ] Include known-absent questions — the system should decline, not invent
- [ ] Score every change against the set instead of judging by vibes
- [ ] Tune: chunk size, k, model size, prompt wording, fusion weights

### Phase 7 — Interface

- [ ] CLI first — answers cite entry dates and print photo file paths
- [ ] Local web UI: chat box, citations that open the source entry, photo thumbnails
      shown inline (the terminal can't, a browser can)
- [ ] Extras: timeline, "on this day N years ago", incremental re-indexing.
      A destinations map is **not** possible — there is no GPS in the archive.

#### The delivery fork: local UI, or connect to a hosted assistant?

Everything through Phase 6 is provider-agnostic, so this defers to here with no rework.

The mechanism is **MCP** — wrap retrieval as a local server exposing
`search_journals(query, year)`; Claude Desktop and Claude Code connect to local MCP
servers over stdio. **The tradeoff:** retrieved entries are sent to the provider as tool
results — that's how the model sees them. The index stays local, but excerpts leave.
Roughly five entries per query rather than the whole archive, which is a meaningful
difference but not zero.

| Option | What leaves the machine | Quality | Effort |
| --- | --- | --- | --- |
| Local UI (Open WebUI / AnythingLLM → Ollama) | Nothing | Good | Low |
| MCP → Claude Desktop | Question + retrieved excerpts | Best | Low–moderate |
| Custom GPT / Actions | Same, plus a public HTTPS endpoint | Best | High |

Skip the third — exposing a tunnel to a personal journal index is real attack surface
for no gain over MCP.

**Middle path:** use the local model as a *router*. It reads each question first, and
only ones classified non-sensitive get escalated. Keeps the default private and makes
the exception deliberate.

### Phase 8 — Vision captioning (enhancement)

Deliberately last. EXIF (Phase 2) delivers most of the value photos have to offer at a
fraction of the cost, and it should be proven useful before spending compute on every
image in the archive.

- [ ] Batch-caption every photo with `gemma4:12b` — already downloaded, already
      `vision`-capable with a CLIP projector, no extra model required
- [ ] Capture OCR text in the same pass: restaurant signs, menus, receipts, tickets.
      A place name may exist *only* on a shopfront in a photo and nowhere in the prose.
- [ ] Embed captions alongside journal chunks so photos are searchable in the same
      vector space — no separate image-embedding infrastructure
- [ ] Spot-check caption quality before trusting it; a wrong caption is a false memory
      inserted into your own archive, which is worse than no caption

A one-off batch job: slow to run, instant to query. Same shape as the Phase 5
extraction pass, and worth running on a sample first to estimate total time.

---

## Licence

MIT — see [LICENSE](LICENSE). The code is yours to adapt; the journals, of course,
are not included.
