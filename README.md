# Journal RAG

Ask natural-language questions about years of personal travel journals — *"Where did I
travel in 2019, and what were the standout meals?"* — answered from the actual text,
running entirely offline on a local LLM.

A learning project, built one phase at a time. The journals themselves are private and
live outside this repository; only code is tracked here.

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

1. **"What were the standout meals?"** — *semantic*. Vector search excels here. The
   journal says "the uni in Vladero ruined me for all other sea urchin" and never uses
   the word "standout." Embeddings catch that; keyword search does not.

2. **"Where did I travel in 2019?"** — *aggregation over a filtered set*. Vector search
   is **bad** at this. It returns the top-k most similar chunks, so if there were nine
   destinations in 2019 and k=5, the answer is confidently incomplete.

Most RAG tutorials build only the first kind, which is why they demo beautifully and
disappoint on a real archive. Phases 1–4 build the semantic path and the fundamentals.
**Phase 5** — metadata filtering, keyword search fused with vectors, and an offline
extraction pass into a structured facts table — is what makes the second kind work.

There is a sharper edge to (1) than it first appears, measured during setup:
**embeddings capture topic, not valence.** "We ate a forgettable sandwich at the
airport" scores *higher* against the query "outstanding food I had" than "the uni in
Vladero ruined me for all other sea urchin" does. Both are about food; the vector
space does not encode which one is praise. So even the semantic path only narrows to
*subject matter* — deciding which meals were actually good falls to the LLM reading
the entries, or to the structured extraction in Phase 5. See
[`docs/SETUP.md`](docs/SETUP.md) for the numbers.

## Architecture

```
.doc/.docx  ──►  clean text  ──►  entry-level chunks + metadata
                                          │
   photos  ──►  EXIF (date, GPS)  ────────┤
      │         offline geocode           │
      └──►  vision captions ──────────────┤
            (gemma4, batch)               │
                        ┌─────────────────┼─────────────────┐
                        ▼                 ▼                 ▼
                  vector index      keyword index      facts table
                   (semantic)          (BM25)         (trips, places,
                        │                 │            dishes, dates)
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

**EXIF is the highest-value piece of this entire project, and it needs no AI.** Every
photo carries `DateTimeOriginal` and usually GPS coordinates. That is a *verified*
travel timeline — dates, cities, countries — independent of whether anything was
written down, and it directly solves the weakness identified above: "where did I travel
in 2019" needs hard structured data, and photo EXIF is exactly that. It cannot be vague
or misremembered, and it cross-checks the text: if the writing says "Tuesday" and the
photos say June 14, the photos win.

> **Reverse geocoding must be offline.** Turning coordinates into place names via
> Google or Nominatim would transmit the GPS location of everywhere you have been,
> including home. Use the `reverse_geocoder` package, which ships a local cities
> database. This is the single easiest way to accidentally undo the privacy premise of
> the project.

**Vision captioning** is the second tier. `gemma4:12b` already reports `vision`
capability with a CLIP projector — no extra model to download. Run each photo through
it once, offline, and store the description ("a bowl of pho with herbs, street-side
plastic stools") as text embedded alongside journal chunks. Photos then become
searchable in the same vector space as the writing, with no separate image-embedding
infrastructure.

It also does OCR, which matters more than it sounds: restaurant signs, menus, receipts,
train tickets. "Pho Thin" may exist only on a shopfront in a photograph and never in
the text.

**Association** — linking a photo to the entry it belongs to — depends on where the
photos live:

| Case | Linking method | Quality |
| --- | --- | --- |
| Journal links the photo | External hyperlink in the `.docx` — gives both the target path and the paragraph it sits in (`src/docx_links.py`) | Exact |
| Journal written in a hurry, no link | Folder `<year>/<place>` narrows the trip; EXIF timestamp matches the day's entry | Good, looser |

Link targets are parsed rather than resolved by Word, so stale Windows paths
(`file:///C:/Users/me/Journals/2019/Ha%20Long%20Bay/IMG_9876.jpg`) re-root onto the
local archive by keeping the trailing `<year>/<place>/<filename>`.

Practical notes: iPhone photos are usually **HEIC** and need `pillow-heif` to read; and
any photo shared with "remove location data" has no GPS.

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
    Japan - 2018.doc                "<place> - <YYYY>.doc(x)"
    ...
    2019/                        ← media, foldered by year
        Strelsau/                       then by place
            IMG_1234.jpg
            clip.mov
        Ha Long Bay/
    2018/
        Klow/
```

This layout does a lot of work for us:

**The year is in the filename.** `Ruritania - 2019.docx` all but eliminates the
year-inference problem from Phase 2. Rollover detection remains as a safety check for
trips crossing New Year, but the base year is known rather than guessed.

**The folder tree maps media → place → year with no inference at all.**
`2019/Strelsau/IMG_1234.jpg` gives location and year without reading EXIF, which means it
still works for photos whose GPS was stripped, and it cross-checks EXIF where both
exist.

**Place folder names are a controlled vocabulary.** A known list of locations to match
prose mentions against in Phase 5, instead of trusting an LLM to spell places
consistently across ten years of entries.

> **Preserve the directory structure when transferring.** Copy the whole archive in
> one operation from the root rather than moving journals and media separately.
>
> Less critical than it first appears: `src/docx_links.py` parses link targets itself
> rather than asking Word to resolve them, keeping the trailing
> `<year>/<place>/<filename>`. So even a stale Windows absolute path
> (`file:///C:/Users/me/Journals/2019/Ha%20Long%20Bay/IMG_9876.jpg`) re-roots onto the
> local archive correctly. Preserving the tree keeps that mapping aligned; it is not
> make-or-break.

#### Photo association: two tiers

Some journals link their photos; others (written in a hurry) do not.

| Case | Method | Quality |
| --- | --- | --- |
| Journal links the photo | Extract the relative path from the `.docx` | Exact — text and image explicitly paired |
| Embedded image | Extract with document position | Exact |
| No link | Folder place + year → narrow to trip; EXIF timestamp → match to that day's entry | Good, looser |

**Videos** (`.mov`, `.mp4`) carry creation-date and often GPS metadata just like stills,
so they feed the timeline identically. Captioning them is a much larger job — frame
sampling — so treat them as timeline evidence only for now.

#### Steps

- [ ] Get the files onto this machine **without routing them through cloud storage.**
      Dropbox/Drive/iCloud is the convenient path and would upload the entire archive
      to a third party — precisely what this project exists to avoid. Use a USB drive,
      a direct Finder network share, or AirDrop between your own devices.
- [ ] Copy the whole tree in one operation, preserving structure (see warning above)
- [ ] Inventory: journal count, year range, `.doc` vs `.docx` split
- [ ] **Count total words/tokens** — determines whether the corpus would even fit in a
      long-context window, which is worth knowing as a correctness baseline to check
      the RAG pipeline against
- [ ] Inventory media: counts per year/place, formats (HEIC needs `pillow-heif`)
- [x] Determine link type: **external hyperlinks** (Word Insert → Link), extracted by
      `src/docx_links.py` — verified against a synthetic journal
- [ ] Convert `.docx` (python-docx / mammoth), preserving headings
- [ ] Extract embedded images and link targets, recording position in the document
- [ ] Convert legacy `.doc` via `textutil`
- [ ] Quality pass: encodings, dropped tables, lost bullets
- [ ] Write a manifest; flag anything that converted badly

*The least glamorous, highest-leverage phase. Garbage here poisons everything downstream.*

### Phase 2 — Chunking and metadata

The source format is:

```
Jun 14   Ha Long Bay
    We took the boat out early. Two days ago in Strelsau I had
    the best pho of my life at a place near the station...
```

A three-letter month and day, an optional title, then the entry. Three consequences,
in increasing order of difficulty.

**1. No year in the header.** `Jun 14` is ambiguous on its own — but the filename
carries it (`Ruritania - 2019.docx`), so this is largely solved. File-level metadata is
load-bearing: any file whose year cannot be parsed from its name becomes a manual
review item rather than a guess.

**2. Year rollover.** Entries running `Dec 28` → `Jan 3` cross a year boundary inside a
single file. Assigning the file's year to every entry would date that January entry
twelve months early, silently. Process entries in order and increment the year whenever
the month moves backwards.

**3. Relative dates — the one that breaks the naive model.** "Two days ago in Strelsau I
had the best pho of my life" means the *event* happened on Jun 12, while the *text*
lives in the Jun 14 entry.

A chunk therefore cannot have a single date. Ask "what did I eat on June 12?" and a
system that only knows entry dates searches the Jun 12 entry, finds no pho, and reports
nothing — while the answer sits two entries later.

**Two date fields, not one:**

| Field | Meaning | Source |
| --- | --- | --- |
| `entry_date` | when it was written | the `Jun 14` header |
| `event_date` | when it actually happened | resolved from the text |

This is a further argument for the Phase 5 extraction pass: resolving "two days ago"
against a known entry date is exactly what an LLM does well and what regex does badly.
The extraction prompt must ask for the event date explicitly.

Photo EXIF corroborates: a photo of pho timestamped Jun 12 confirms the resolution.

- [ ] Chunk by journal **entry**, not character count — fall back to size splits only
      for very long entries
- [ ] Parse the `MMM DD` header; take the year from the filename (`<place> - <YYYY>`)
- [ ] Detect year rollover by watching for the month moving backwards (trips crossing
      New Year) — a safety check now, not the primary mechanism
- [ ] Flag files whose year cannot be parsed from the name — review, don't guess
- [ ] Take the trip location from the filename and the media folder names as a
      controlled vocabulary of places
- [ ] Read photo EXIF: `DateTimeOriginal` + GPS → offline reverse geocode to
      city/country. No AI needed; produces a verified travel timeline.
- [ ] Record `entry_date` for every chunk; leave `event_date` resolution to Phase 5
- [ ] Attach metadata: `entry_date`, `year`, `month`, `source_file`, `trip`,
      `entry_title`
- [ ] Prepend context to chunk text so each is interpretable alone
      ("2019-06-14, Strelsau — the noodles were incredible")

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

### Phase 5 — Hybrid retrieval and structured facts

- [ ] Parse year/date ranges from questions; filter *before* semantic ranking
- [ ] Add BM25 keyword search; fuse rankings with vectors
- [ ] Offline extraction pass: run the local LLM over every entry once, pulling
      `{entry_date, event_date, country, city, dishes, restaurants, people}` into SQLite
- [ ] **Resolve relative dates** ("two days ago", "last Tuesday", "the night before")
      against the entry date, and store the result as `event_date`. Facts are indexed
      by when they *happened*, not by when they were written about.
- [ ] Cross-check resolved dates against photo EXIF where a photo exists
- [ ] Load photo EXIF (dates, coordinates, place names) into the facts table — this is
      ground truth for "where was I when", stronger than anything parsed from prose
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
- [ ] Extras: map of destinations (EXIF GPS makes this nearly free), timeline,
      "on this day N years ago", incremental re-indexing

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
