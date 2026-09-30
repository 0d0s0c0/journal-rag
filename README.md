# Journal RAG

Ask natural-language questions about years of personal travel journals — *"Where did I
travel in 2019?"*, *"What were my favourite meals and where did I have them?"*,
*"What were the best places I snorkelled?"*, *"Which museums did I actually like?"* —
answered from the actual text, running entirely offline on a local LLM.

A learning project, built one phase at a time. The journals themselves are private and
live outside this repository; only code is tracked here.

**Status:** Phases 0–4 complete — 54 documents parsed into 1,708 dated entries with
14,305 photos attached, chunked into 4,042 passages, embedded into a local vector index,
and now **answering questions in natural language**, grounded in the retrieved text with
dated citations, in a median of 9 seconds. It refuses all four test questions about
things that never happened, cites an expected entry for 75% of the real ones (exactly
matching retrieval's recall@1, so nothing is lost in generation), and never cites a
known-wrong entry. Verified to open no network connection but loopback.

Phases 5–8 (the experience index, wider evaluation, interface, vision captioning) are
planned and described below.

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
calls, no telemetry. Verified rather than assumed:

```bash
./scripts/verify-offline.sh              # answer a question, watch every socket
./scripts/verify-offline.sh --self-test  # prove the check is capable of failing
```

The claim being tested is not "a local model was chosen" but "journal text never
leaves the machine" — different claims, and only the second matters. The script
audits every URL in `src/` and samples the process tree's TCP peers while a real
question is answered; anything that is not loopback fails it. Current result: one
connection, to `127.0.0.1:11434`.

Pulling the network cable — the original plan — tests something weaker. It shows
the pipeline *survives* without a network, not that it stays silent when one is
available. And the socket check needed its own negative control: the first version
watched the wrong pid (`uv run` execs python as a child, and the child holds the
socket), reported zero connections, and that looked exactly like a pass.
`--self-test` binds a listener to this machine's own LAN address and connects to
it — a genuinely non-loopback peer with no packet leaving the host — and the
detector has to flag it.

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

### The hook checks three things, because one was not enough

`scripts/hooks/pre-commit` blocks staged changes containing anything derived from
the archive. The pattern lists are rebuilt by `uv run python -m src.private_names`
and live outside the repo:

| | matched as | why |
|---|---|---|
| place names | word boundary, case-insensitive | writing *about* a private archive means reaching for its real names as the obvious illustration |
| entry dates | exact, never inside an ISO timestamp | a date beside a description identifies an entry as surely as a name |
| episode wording | phrase as substring; rare word on word boundary | a question describing what happened is archive content too |

Only the first existed at first, and it was half a defence. Real entry dates and
real episode descriptions accumulated in eval examples and test fixtures, tripped
nothing, and reached a public repo.

The word rule is the interesting one, because the obvious version does not work.
Blocking every word from a question would reject ordinary prose; blocking none
misses the single words that leaked. What separates them is measurable — how many
entries contain the word:

```
     3  a person's name        <- identifies one entry
    11  a specific dish        <- identifies one entry
   283  "tire"                 <- describes a category
   388  "bike"                 <- describes a category
```

So a word is blocked when it appears in at most 15 entries. The gap is wide and
nothing in the question set falls inside it. Words like *tire* and *bike* stay
usable individually — while the **phrase** they form is blocked, which is what
actually identified the episode.

Two failures worth keeping:

- The timestamp guard exists because an earlier unguarded date rule rewrote a
  PyPI `upload-time` inside `uv.lock` during history cleanup. A date is only a
  date when nothing follows it.
- Writing this check leaked data. The commit adding it was blocked by itself: the
  explanatory comments used a real date and a real episode as the illustration.
  That is the failure mode in miniature, and the reason the rule cannot live in
  anyone's memory.

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
filename's trip label instead (`2019-08-19, Ruritania`), which cannot be wrong.

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
      1,741 entries (1,708 converted + 33 reconstructed) -> **4,042 chunks**
      (2.32 per entry) at `max_chars: 1600`; 51% stay whole.
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

#### "Which entries mention X" is not a ranked question

Searching a single surname returned one entry. It occurs in **four**, in four
unrelated trips across four years. Measured:

| | entries found (of 4) |
|---|---|
| vector, k=5 | 1 |
| vector, k=25 | **still 1** |
| BM25 | 4, at ranks 1–4 |
| hybrid, k=10 | 4 |

Raising `k` does nothing, so this is not a depth problem. A bare proper noun gives
the embedding almost no signal, and each of those four chunks is dominated by
whatever else its entry is about — the top vector hit scored 0.470 with ranks 2–5
between 0.356 and 0.394, none of them containing the term at all.

The instinct is to switch to hybrid. Measurement says it is not that simple — no
RRF weighting wins both, the tradeoff is strictly monotone:

| weights (vec:fts) | recall@1 | recall@10 | the 4-entry name query |
|---|---|---|---|
| 1:0 vector only | **0.77** | 0.85 | 1 of 4 |
| 3:1 | 0.62 | 0.85 | 2 of 4 |
| 1:1 hybrid | 0.54 | **0.92** | 3 of 4 |
| 1:2 | 0.54 | 0.85 | 4 of 4 |
| 0:1 fts only | 0.46 | 0.69 | 4 of 4 |

I had earlier concluded BM25 "didn't pay" on this archive. That was measured on an
eval set with barely any lexical queries in it, so the conclusion was scoped to
that set and stated as if it were general. It is not.

The deeper point is that *neither* retriever can answer the question as asked.
Top-k returns k rows whether a term occurs twice or forty times, so a complete
answer is outside its contract. That needs a different operation:

```bash
uv run python -m src.search "Margarethe" --all   # every entry, unranked, no k
# 4 entries, 4 chunk(s), 5 occurrence(s) — complete, not top-5
```

Two details that mattered: the SQL `LIKE` pre-filter is a *substring* test and
matched an unrelated word sharing those letters, so a whole-word pass runs behind
it (`--substring` keeps the looser behaviour for stem searches); and LanceDB's
`limit` has a non-`None` default, so an exhaustive scan has to say so explicitly
or it silently returns the first handful — the exact bug class this feature exists
to fix.

Vector stays the default, and a short query now gets a one-line warning when the
term occurs literally in entries the ranking will not return.

#### A year in the question is a filter, not a hint

*"Where did I snorkel in 2019"* returned three places. Sixteen entries that year
mention it. Two separate causes, and the first was an outright bug: the search CLI
detected a year in the query and filtered on it, but `ask` never did — so **2 of
the 5 excerpts were not even from 2019**, and the model answered from the others.
The embedding does not encode dates, and putting "2022" in the prompt does nothing.

Filtering runs before ranking, so it is exact and free. It doubles coverage at
every depth:

| | k=5 | k=10 | k=25 | k=50 |
|---|---|---|---|---|
| no filter | 12% | 25% | 44% | 75% |
| `year=2019` | 25% | 50% | 88% | **100%** |

#### More context is not more coverage

With retrieval fixed, the obvious move is to raise `k`. It works, and then it
stops working. Three runs at each setting, counting entries the answer actually
cited out of 16:

| k | entries cited | time |
|---|---|---|
| 5 | 4 | 18s |
| 10 | 8 | 27s |
| **25** | **13** | 45s |
| 50 (`num_ctx` 16384) | 12 | 76s |

k=50 retrieves **all 16** and cites fewer than k=25, reproducibly. Past a point,
extra excerpts dilute the summary rather than extend it — so completeness is not a
retrieval-depth problem and cannot be bought with a bigger context window. That is
the argument for the Phase 5 experience index: extract once, then query structure.

Two things did come out of it. The year filter now applies in `ask` (`--year 0`
disables it), and an answer that is a sample now says so:

```
cited: 2019-04-11, 2019-04-13, 2019-09-02, 2019-09-06
COVERAGE: 16 entries mention 'snorkel' in 2019; this answer cites 4.
          It is a top-5 sample, not a complete list.
```

An incomplete answer and a complete one are indistinguishable in prose. That line
costs ~20 ms of index scanning and removes the ambiguity.

Worth noting what the model got *right* here: at k=25 it flagged two places it had
read about but not snorkelled at — "you decided not to snorkel here" and one where
the entry records being too lethargic to bother. That is the Phase 4 reading work
holding up on real data, not a test fixture.

#### Dense retrieval's one catastrophic failure

Asked *"the time i won a tombola"*, the system said it wasn't in the journals.
Adding three words of context — *"at the Vespugia baths"* — returned the right
entry at rank 1.

The entry contains the word **tombola**. It is one of only three in the archive
that do. Vector search did not return it in the **top 100**:

| retriever | rank of the correct entry |
|---|---|
| vector | **not in top 100** |
| BM25 | 4 |
| hybrid | 9 |

A rare word inside a 1,600-character chunk barely moves the embedding, so the
chunk ranks on everything else it is about. Adding context fixed it by giving the
embedding something to work with — which is not something a person should have to
know in order to search their own diary.

Switching retriever doesn't fix it either: hybrid ranks it 9th, still outside the
default k=5, and BM25 alone costs recall@1 0.77 → 0.46 across the eval set. So
instead, **two of the five slots are reserved for chunks containing a rare literal
term from the query** (`retrieval.rare_slots`). A floor, not a quota — unused when
the query has no rare term. On the eval set it changed recall@1, recall@5 and
misses not at all, and moved completeness@5 from 0.54 to 0.58.

Reserving the slots was the easy part. Deciding *what goes in them* took four
attempts, every correction forced by a query that failed rather than by review:

1. **Filled in query order.** A question naming two rare terms gave both slots to
   whichever came first, and one went to an entry about the same subject in an
   entirely different country. It displaced two ranked results and flipped an
   answerable question from cited 4/4 to **refused 4/4**.
2. **Ranked by mention count.** Better, but useless for a noun. Asked about a
   *tiffin*, all **nine** entries containing the word mentioned it exactly once —
   a perfect tie — so the slots went to the first two by date and the right entry,
   sixth in that arbitrary order, got nothing.
3. **Ranked by similarity to the query.** The fix, and the reason the whole
   mechanism works:

   | | that entry |
   |---|---|
   | similarity to the query | 0.487 — weak |
   | rank among all 4,042 chunks | 9th — outside any sane window |
   | rank among the 9 containing the word | **1st**, 0.487 vs 0.422 |

   Neither signal locates it alone. The lexical filter narrows to nine, the
   vector chooses among them. My earlier version did the narrowing and then threw
   the ranking away.
4. **One chunk per entry.** Ranking purely by similarity then spent both slots on
   two chunks of the *same day*, surfacing one entry where the slots exist to
   surface entries the ranker missed.

Term coverage still outranks similarity — a chunk containing two of the query's
rare terms beats one that is merely a closer embedding.

#### Every rule pushed toward caution, and nothing pushed back

With retrieval fixed, the question still failed — now at the generation step. The
entry describes picking tickets and the attendant clapping, but never uses the
word *won*, so the model answered "I can't find that in the journals" **3 times
out of 3** while holding the correct excerpt.

That is the accumulated grounding rules working exactly as written. Each one was
added to stop a specific over-claim, and together they left no way to say "here is
the relevant entry, and here is what it does not establish". One rule was missing:

> Refusing is for when the excerpts contain nothing relevant. If an excerpt is
> clearly about what the question asks but does not settle it, give that excerpt
> and say what it does not establish. Do not refuse in that case.

Measured before adopting it, 3 runs each — the absent questions are the guard rail,
because a rule that makes the model *less* willing to refuse is exactly the kind
that could destroy the 100%:

| | before | after |
|---|---|---|
| the affected question | refuse 3/3 | **cite 3/3** |
| a second unstable question | cite, off, cite | cite 3/3 |
| refusal on things that never happened | 12/12 | **12/12** |

#### recall@1 was hiding this

The harness scored that query as a **success**. Its ground truth listed 2 entries,
retrieval put one at rank 1, and `recall@1` asks only "did something correct rank
first". It printed `[1/4 found]` beside the result and then averaged that away.

`completeness@k` now reports it directly — what fraction of *all* expected entries
were surfaced, over the 8 of 13 questions that expect more than one:

```
recall@1          0.77
completeness@10   0.71   (17/24 expected entries surfaced)
```

A metric that answers the question you are actually asking is worth more than a
better score on one that doesn't.

### Phase 4 — First working RAG

```bash
uv run python -m src.ask "the name of the famous roast pork dish in Zenda"
# Porchetta (2020-01-03)
```

#### A score threshold cannot protect against absent questions

Measured on this archive, the top-hit similarity for questions with a real answer
and for questions about things that never happened **overlap almost completely**:

```
real answers    0.509 ───────────────────────── 0.900
never happened       0.578 ──────── 0.760
```

Six real answers score *below* the highest absent one. Any cutoff that rejects
"the time I ran a marathon" also rejects six questions that have answers. So the
refusal has to come from the model reading the excerpts and noticing the answer
is not there — which it does: **4 of 4 absent questions refused**, including the
two that would have passed any threshold.

#### Thinking mode silently ate every answer

The single most expensive bug so far, and it did not look like a bug. Generation
would sometimes take minutes and return an empty string; `num_predict: 60`
returned empty strings every time. Measured on one question (1,593-token prompt):

| | tokens generated | wall time | answer returned |
|---|---|---|---|
| thinking default | 6,599 | 429s | **empty** |
| `think: false` | 83 | 14.4s | correct, correctly cited |

Ollama's `/api/generate` returned neither `response` **nor** `thinking` — seven
minutes of reasoning, discarded. And an empty answer reads exactly like a
refusal, so the failure impersonated the feature built to catch hallucination.

`generation.think: false` is now the default, and `generate()` raises if it ever
sees tokens generated with no text returned rather than handing back `""`.

Two related fixes came out of chasing it:

- **Generation streams.** A non-streaming request cannot distinguish a slow
  answer from a hung server; ten minutes of silence produced no output at all.
  The timeout is now per-read, so a real stall trips in seconds.
- **A killed client does not stop the work.** Ollama keeps generating after the
  request is abandoned, and `-np 1` means one slot — so every later request
  queues behind the orphan. A cascade of "timeouts" turned out to be one
  abandoned request blocking the rest. `ollama stop` will not clear it while it
  is mid-generation; the runner has to be killed.

#### Context windows are not free

Ollama loaded the 639 MB embedding model with a **32K** window costing 4.0 GB of
KV cache, alongside a 16K chat window — 12.1 GB resident on a 34 GB machine, and
it swapped. The longest chunk in this archive is **417 tokens**, so the embedding
window is now 1,024 and the chat window 8,192: 9.4 GB, and the same question went
from 176s to 41s.

Shrinking an embedding window is only safe if nothing was being truncated, so
that was checked rather than assumed — the longest chunks re-embed to cosine
1.000000 against their stored vectors, so the index did not need rebuilding.

#### A question can smuggle in a premise the journals never confirm

Asking *"which places did I snorkel at on \<one island of an archipelago\>?"*
returned a confident list of beaches — on the wrong islands. The trip labels are
region-level (the archipelago, not the island), the archive has no GPS, and the
entries rarely name the island, so nothing in the excerpts supported the island
named in the question. The model accepted the premise because the question
asserted it.

The fix is a prompt rule, and its wording matters more than expected:

| rule | answerable place question | unconfirmable place question |
|---|---|---|
| none | cites correctly 3/3 | **invents 3/3** |
| "check the excerpts confirm it" | **refuses 3/3** | refuses 3/3 |
| "check the excerpts *mention* it" | cites correctly 3/3 | refuses 2/3 |

The broad wording fired on any named place and refused a question retrieval had
answered at rank 1. Asking the concrete, checkable question — do the excerpts
*mention* it? — keeps those answers and still catches most false premises.

One case stays unfixable by prompting: a show seen at a named theatre that the
journal never places in the district the question asks about. Resolving it needs
to know the venue is in that district, which is exactly the world knowledge the
grounding rules forbid. Logged for Phase 5, not papered over.

#### Measured end to end

`uv run python -m src.evaluate --generate` now scores the answer, not just the
retrieval — because retrieval metrics cannot see either failure that matters
once a model is in the loop:

```
refused when absent      4/4   (100%)   <- the one that matters
cited an expected date   9/12  (75%)
cited a known-wrong date 0/12
refused a real question  3/12           <- 2 are genuine retrieval misses
answered with no date    0/12
median time              9.4s
```

75% grounded exactly equals recall@1, so generation gives up nothing retrieval
found. Zero known-wrong citations, with seven known-wrong entries sitting in the
retrieval window.

- [x] Assemble the prompt: question + retrieved chunks + instructions
- [x] Ground it hard — answer only from provided entries, say "I can't find that
      in the journals" rather than guess
- [x] Cite the source entry date for every claim; warn when an answer cites none
- [x] Set `num_ctx` explicitly — Ollama's default truncates the prompt silently
- [x] Disable thinking mode, and fail loudly instead of returning an empty answer
- [x] Stream generation so a slow answer is distinguishable from a hung server
- [x] Refuse premises the excerpts do not support, without refusing real questions
- [x] Verify offline operation — `scripts/verify-offline.sh`
- [x] Measure refusal and citation rates as part of the eval harness

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

Every chunk already carries a synthetic header, `2016-09-24, elbonia — `, stamped from
the journal's filename during chunking. It was added so a retrieved fragment would be
self-contained; it turns out to do a second job.

Tested with `gemma4:12b` on two place names from the archive that are ambiguous
worldwide — a canyon and a historic town, each of which exists in several countries:

```
place name alone   -> confidently wrong continent, or "not a recognized location"
place name + trip  -> correct state, on both
```


A place name alone is ambiguous worldwide. With the trip label the model resolves it,
and the label is usefully sized: foreign trips give a country, US trips give a state —
narrow enough to disambiguate, broad enough to be reliably correct.

**So the extraction prompt must include the header, not just the body.** Without it,
a canyon name gets filed under the wrong country, and the facts table is
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
