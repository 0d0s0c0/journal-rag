# Setup Log

Step-by-step record of building this environment, including the things that went
wrong and how they were resolved. Written to be re-runnable on a fresh machine.

Target machine: Apple M1 Max, 32 GB RAM, macOS (Darwin 25.5).

---

## Phase 0 — Environment

### 0.0 Repository, privacy-first

The repo is private now and intended to go public. Journals must never enter it.
The `.gitignore` is committed **before** any data exists nearby, so there is no
window where a stray file could be staged.

```bash
cd ~/playground/journal
git init -b main
git add .gitignore README.md config.example.yaml
git status                       # read it — confirm exactly these files
git commit -m "Initial commit: README, gitignore, config template"
```

Data lives **outside** the repo, as a sibling:

```bash
mkdir -p ~/playground/journal-data/{raw,text,index,eval}
cp config.example.yaml config.yaml      # gitignored; holds the real path
git status                              # must be clean
```

Verify the ignore rules actually work rather than trusting them:

```bash
git check-ignore -v config.yaml
# .gitignore:59:config.yaml    config.yaml
```

`journal-data/` never appears in `git status` at all — it is outside the repo
boundary. That, not `.gitignore`, is the primary defense.

> **Check first:** this only works because `~/playground` is not itself a git repo.
> If it were, a "sibling" directory would be *inside* a repo and the whole scheme
> would fail silently.
> ```bash
> [ -d ~/playground/.git ] && echo "PROBLEM: parent is a repo"
> ```

#### Gotcha: git identity

`git config user.name` / `user.email` were unset globally. Git does **not** block the
commit — it guesses from the OS and commits as `Name <user@Hostname.local>`. Harmless
locally, but those commits are not linked to a GitHub account, and the machine hostname
ends up permanently in the history of a repo intended to go public.

```bash
git config --global user.name "Your Name"
git config --global user.email "USERNAME@users.noreply.github.com"
```

The `users.noreply.github.com` form keeps a real address out of public commit metadata
while still attributing commits to the account. Find the exact address (some are
ID-prefixed) under GitHub → Settings → Emails.

#### Remote

```bash
git remote add origin https://github.com/USER/journal-rag.git
git push -u origin main
```

HTTPS works without a token prompt if `credential.helper=osxkeychain` is set and a
GitHub credential is already cached from earlier work:

```bash
git config --system credential.helper           # osxkeychain
security find-internet-password -s github.com   # confirms a cached entry
```

---

### 0.1 Install `uv` and Ollama

```bash
brew install uv ollama
```

- **`uv`** — Python version and package manager. Installs Python versions independently
  of Homebrew's, which matters because the system Python here is 3.14 and much of the ML
  ecosystem still lags on the newest releases. Its resolver is also fast enough that
  rebuilding the environment isn't a chore.
- **`ollama`** — local model server. Serves models over `localhost:11434` with an API
  shaped like the hosted ones, so switching to a hosted model later is a config change
  rather than a rewrite. On Apple Silicon it now pulls in **MLX** as a dependency.

Formula, not cask — the cask adds a menubar GUI that makes things less scriptable.

```bash
uv --version
ollama --version
```

#### Gotcha: the brew service never starts

`brew services start ollama` reported success, but nothing listened on 11434 and no log
file was ever created.

```bash
launchctl print gui/$UID/sh.brew.ollama | grep -E 'state|runs|last exit'
#   state = not running
#   runs = 0
#   last exit code = (never exited)
```

The job was registered, enabled, and had both `KeepAlive` and `RunAtLoad` set — launchd
simply never executed it. Not an Ollama fault; the binary runs fine directly. Rather
than fight launchd, run the server in the foreground, which is better during development
anyway since you can watch requests arrive:

```bash
./scripts/ollama-serve.sh
```

That script sets the tuning env vars (see below) and refuses to start a second instance.

#### Server tuning

`scripts/ollama-serve.sh` exports three variables:

| Variable | Value | Why |
| --- | --- | --- |
| `OLLAMA_FLASH_ATTENTION` | `1` | Faster attention, smaller memory footprint. Pays off at long context — which RAG produces constantly. |
| `OLLAMA_KV_CACHE_TYPE` | `q8_0` | The KV cache grows with context and is often the real memory ceiling, not the weights. 8-bit roughly halves it. |
| `OLLAMA_KEEP_ALIVE` | `30m` | Default 5m means a coffee break costs a multi-GB reload from disk. This is the fix for apparent "slowness" that is really a cold start. |

The first two match what Homebrew's own service plist sets.

Verify the server:

```bash
curl -s http://localhost:11434/api/version
# {"version":"0.34.2"}
```

#### Gotcha: Ctrl-C can leave the port bound

Ctrl-C does not always terminate `ollama serve` cleanly — it can wedge mid-shutdown,
still holding the socket but no longer answering requests. The next start then fails
with:

```
Error: listen tcp 127.0.0.1:11434: bind: address already in use
```

Diagnose and clear it:

```bash
lsof -nP -iTCP:11434          # shows the PID still holding the port
pgrep -fl ollama              # parent `ollama serve` plus any llama-server child
pkill -f "ollama serve"       # SIGTERM — observed here NOT to be enough
lsof -t -nP -iTCP:11434 | xargs -r kill -9   # SIGKILL; this worked
```

The wedged process ignores SIGTERM, so expect to need SIGKILL. The `llama-server`
child terminates with its parent — no orphan to clean up separately.

`scripts/ollama-serve.sh` checks for a live server before starting and reports it
clearly instead of surfacing the raw bind error — but it can't detect this case, where
the port is held by a process that no longer responds.

---

### 0.2 Pull models

```bash
ollama pull gemma4:12b
ollama pull qwen3-embedding:0.6b
ollama list
```

~8.2 GB total.

**Chat — `gemma4:12b`** (7.6 GB, 256K context, Apache 2.0). Deliberately *not* the
largest model this machine can run. Until Phase 6 provides an eval set there is no way
to tell whether a bigger model answers better, and a small fast model makes Phases 1–5
much quicker to iterate on. Upgrading later is one `ollama pull` plus a line in
`config.yaml`.

The 256K context window is the reason this family fits RAG well — retrieval quality is
bounded partly by how many retrieved entries fit in the prompt.

Candidate for the Phase 6 comparison: `gemma4:26b` (19 GB) is mixture-of-experts with
only 4B active parameters, so it runs far faster than its size implies.

#### Gotcha: Ollama does not use the model's full context by default

A model advertising 256K does **not** mean you get 256K. Ollama loads with a
conservative default — observed here by inspecting the running process:

```bash
pgrep -fl llama-server
# …llama-server --model …gemma4… -c 32768 …
```

`-c 32768` — 32K, not 256K, despite gemma4 supporting the larger window.

This is a classic RAG failure mode. You retrieve ten entries, assemble a prompt that
exceeds the loaded window, and the model silently sees only part of it. **No error is
raised** — you just get a confidently wrong answer built from truncated context, and
nothing in the output indicates anything was dropped.

The fix is to set `num_ctx` explicitly in the API request options (or via a Modelfile)
rather than relying on the default:

```json
{ "model": "gemma4:12b", "prompt": "...", "options": { "num_ctx": 65536 } }
```

Do this in Phase 4 and keep the value in `config.yaml`.

Bigger is not automatically better: the KV cache grows with context length and is
usually the real memory ceiling, not the weights — which is what
`OLLAMA_KV_CACHE_TYPE=q8_0` in the serve script exists to mitigate. Size `num_ctx`
against the actual volume of retrieved chunks, which Phase 3 will reveal.

**Worth verifying rather than assuming**, whenever answers look oddly incomplete:

```bash
pgrep -fl llama-server | grep -o '\-c [0-9]*'
```

**Embeddings — `qwen3-embedding:0.6b`** (639 MB, 32K context, Apache 2.0). Small, fast,
from the family that topped the MTEB multilingual leaderboard.

The 32K context matters more than it appears: a journal entry longer than the embedding
model's window is **silently truncated**, and the tail simply stops being findable with
no error to warn you.

> Changing the embedding model later means re-embedding the whole corpus — the vector
> dimensions change and the existing index becomes invalid. Minutes for a personal
> archive, so not lock-in, but not a casual mid-phase swap either.

**Licensing:** Gemma 4 is Apache 2.0 (a change from Gemma 1–3, which used Google's
custom terms with a prohibited-use policy). Qwen3 is Apache 2.0 as well. The whole
stack is permissively licensed, free, and runs with no account, no API key, no rate
limits, and no data leaving the machine.

---

### 0.3 Smoke test

Chat:

```bash
ollama run gemma4:12b "In one sentence: what is retrieval-augmented generation?"
```

Watch the delay before the first token versus the rest — that gap is the model loading
into unified memory, and it only happens on a cold start. Don't mistake it for the model
being slow.

Embeddings, via raw HTTP rather than a Python wrapper, so you're exercising the same API
your code will use and can tell server problems apart from code problems:

```bash
curl -s http://localhost:11434/api/embed \
  -d '{"model":"qwen3-embedding:0.6b","input":"pho in Strelsau"}' | head -c 200
```

The array of floats that comes back *is* the core idea of the project: text converted
into a point in high-dimensional space — 1024 dimensions for this model. Retrieval is
just "find the nearest points."

#### Gotcha: Qwen3-Embedding requires an instruction prefix on queries

Qwen3-Embedding is *instruction-aware*: a short task description prepended to the query
steers where the text lands in vector space. Without it, discrimination between
candidate passages degrades badly, and nothing warns you.

The prefix is not cosmetic — the same query embedded bare vs. prefixed gives
`cos = 0.60`, i.e. a substantially different vector.

Measured against three toy sentences, query `"outstanding food I had"`:

```
WITHOUT instruction prefix   (spread: 0.0523)
  0.5807  we ate a forgettable sandwich at the airport
  0.5293  the uni in Vladero ruined me for all other sea urchin
  0.5285  the hotel wifi was down all morning

WITH instruction prefix      (spread: 0.1900)
  0.4384  we ate a forgettable sandwich at the airport
  0.4309  the uni in Vladero ruined me for all other sea urchin
  0.2484  the hotel wifi was down all morning
```

Without the prefix everything sits inside a 0.05 band — the model barely separates
"sea urchin" from "wifi was down". That is not working retrieval, and built upon
naively it yields mediocre results with no visible cause. With the prefix the spread
nearly quadruples and the irrelevant entry falls away cleanly.

Prefix format:

```
Instruct: {task description}
Query: {the actual query}
```

**Applies to queries only, not to the documents being indexed.** Stored passages are
embedded bare. Getting this backwards silently degrades the whole index.

#### Finding: embeddings capture topic, not valence

Even with the prefix, `"forgettable sandwich"` (0.4384) still scores *above*
`"the uni ruined me for all other sea urchin"` (0.4309). Both are about food, and the
embedding model does not encode that one is praise and the other a complaint.

Consequence for this project: the motivating question — *"what were some outstanding
foods I had?"* — **cannot be answered by vector search alone**. Retrieval surfaces
food-related entries; judging which were *good* has to come from the LLM reading them
(Phase 4) or from the structured extraction pass (Phase 5).

This is a sharper form of the two-query-types argument in the README: semantic search
finds *subject matter*, not *judgment*.

> Evidence caveat: three toy sentences. Real journal entries are longer and more
> distinctive, which generally helps. The instruction-prefix effect is solid and
> reproducible; the valence finding should be re-tested against the real corpus in
> Phase 3.

#### Tested and *not* supported: the "query/passage asymmetry" story

The common explanation for instruction prefixes is that queries otherwise cluster with
other *queries* rather than with the passages that answer them, and the prefix corrects
this. Tested directly, that did not reproduce with this model:

```
bare query   vs similar question: 0.6718   vs answer passage: 0.5293  -> closer to the QUESTION
with prefix  vs similar question: 0.6450   vs answer passage: 0.4309  -> closer to the QUESTION
```

The prefix never flipped the ordering. It is also a badly-posed test: a real index
contains only journal entries, never questions, so a query's similarity to another
question is not a competitor that exists in practice.

Recorded here so the mechanism isn't repeated as fact. **The justification for using
the prefix is the measured improvement in ranking among real candidate passages above
— not this story.**

---

### 0.4 Python environment

Python **3.14** — chosen by testing, not reputation. The whole dependency set resolves
*and* installs from prebuilt wheels on 3.14 with nothing compiled from source:

```bash
uv pip compile reqs.in --python-version 3.14   # 109 packages, resolves
uv pip install -r reqs.in                      # no "Building" lines = all wheels
```

Earlier advice to pin 3.12 "to avoid ML wheel gaps" was stale and was dropped. Verify
rather than assume; the ecosystem moves.

```bash
uv init --bare --python 3.14
uv add lancedb python-docx mammoth pyyaml pandas rank-bm25 httpx \
       jupyterlab ipykernel nbstripout
uv run python -c "import lancedb, docx, pandas; print('env ok')"
```

`uv run` activates the environment automatically — no `source .venv/bin/activate`.

**Commit `uv.lock`.** It pins exact versions and is what makes the environment
reproducible on another machine.

**LanceDB over Chroma:** Chroma sends anonymized usage telemetry by default. It can be
disabled, but "remember to turn off the phoning-home" is a poor fit for a project whose
premise is that nothing leaves the machine. A privacy-posture call, not a performance
one.

---

### 0.5 nbstripout — the leak vector `.gitignore` cannot cover

Notebook *outputs* are stored inside the `.ipynb` file. Print a retrieved journal entry
in a notebook and that text is now in a file you legitimately want to track.
`.gitignore` is no help — the notebook itself should be committed; only its outputs are
toxic.

```bash
uv run nbstripout --install --attributes .gitattributes
```

This writes a clean filter into `.git/config` and registers `*.ipynb` in
`.gitattributes`.

**Verify it, don't trust it.** Stage a notebook containing a marker string and inspect
what git actually recorded:

```bash
git add notebook.ipynb
git show :notebook.ipynb | grep MARKER   # should find nothing
```

Confirmed here: text present on disk, `outputs: []` and `execution_count: None` in the
staged blob.

> **Gap to know about:** the filter definition lives in `.git/config`, which is **not
> committed**. `.gitattributes` alone does not protect a fresh clone — with no filter
> defined, git passes content through unstripped and says nothing. Re-run
> `uv run nbstripout --install` after every clone, on every machine.

---

### 0.6 Pre-commit hook

`scripts/hooks/pre-commit` is the last backstop, for the mistake the other layers miss:
`git add -A` at the wrong moment, or a fresh clone with no local protections. It blocks
three things and warns on a fourth:

1. Journal formats and derived artifacts by extension
   (`.doc .docx .rtf .odt .pages .pdf .txt .db .sqlite .lance .parquet .npy .pkl`)
2. Paths reserved for data (`data/ journals/ index/ embeddings/ eval/ logs/ cache/`)
3. Notebooks whose **staged blob** still contains outputs
4. Warns on any staged file over 5 MB — a weak proxy for "this is data, not code"

Installed via `core.hooksPath`, so the hook lives in the tree and is version
controlled, unlike `.git/hooks/`.

#### After cloning, run this — nothing else installs it

```bash
./scripts/setup-repo.sh
```

Both protections live in `.git/config`, which is **not committed**. A fresh clone has
neither until this is run.

#### Verified, not assumed

| Test | Result |
| --- | --- |
| `git add -f "Japan 2019.docx"` → commit | **blocked** |
| Notebook with outputs, no filter configured | **blocked** by hook |
| Notebook with outputs, filter configured | never even stages (see below) |
| Ordinary source file | commits normally |

#### Finding: `filter.nbstripout.required = true` is stronger than expected

With the filter configured, a notebook whose clean filter fails is **refused at
`git add`** outright — `fatal: t.ipynb: clean filter 'nbstripout' failed`. It never
reaches the index, so the hook never sees it.

That is good, but it is exactly why the fresh-clone case needed separate testing: with
*no* `filter.nbstripout.*` config at all, `required` does not exist either, git passes
the notebook through silently, and only the hook stands between outputs and history.
Simulated with `git config --remove-section filter.nbstripout` and confirmed the hook
catches it.

---

### 0.7 Remaining

- [ ] Disable editor/library telemetry

---

## Phase 1 — Ingestion

Not started. Blocked on one question: **where do the journals live and how are they
organized** — one file per trip, per year, or one large file? That determines the
chunking strategy more than any other factor.
