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
                     answer + citations to entries
```

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
- [ ] Add pre-commit hook rejecting journal file types
- [ ] Turn off editor/library telemetry

### Phase 1 — Ingestion

- [ ] Inventory the journals: count, date range, how they're organized
- [ ] Convert `.docx` (python-docx / mammoth), preserving headings
- [ ] Convert legacy `.doc` via `textutil`
- [ ] Quality pass: encodings, dropped tables, lost bullets
- [ ] Write a manifest; flag anything that converted badly

*The least glamorous, highest-leverage phase. Garbage here poisons everything downstream.*

### Phase 2 — Chunking and metadata

- [ ] Chunk by journal **entry**, not character count — fall back to size splits only
      for very long entries
- [ ] Extract a date for every chunk; flag undated ones for review
- [ ] Attach metadata: `date`, `year`, `month`, `source_file`, `trip`, `entry_title`
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
      `{date, country, city, dishes, restaurants, people}` into SQLite
- [ ] Route by question type: aggregation → facts table, open recall → hybrid search

*Where this stops being a tutorial and becomes useful.*

### Phase 6 — Evaluation

- [ ] Grow the question set to 30–50, spanning both query types
- [ ] Include known-absent questions — the system should decline, not invent
- [ ] Score every change against the set instead of judging by vibes
- [ ] Tune: chunk size, k, model size, prompt wording, fusion weights

### Phase 7 — Interface

- [ ] CLI first
- [ ] Local web UI: chat box, citations that open the source entry
- [ ] Extras: map of destinations, timeline, "on this day N years ago",
      incremental re-indexing

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
