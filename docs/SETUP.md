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
into a point in high-dimensional space, where "outstanding meal" and "the uni ruined me
for all other sea urchin" land near each other despite sharing no words.

---

### 0.4 Remaining

- [ ] Python environment via `uv`, pinned to a version with solid ML wheel coverage
- [ ] `nbstripout` registered as a git filter — **before** the first notebook exists.
      Notebook *outputs* capture retrieved journal excerpts and are saved inside the
      tracked `.ipynb`. `.gitignore` cannot help; the notebook is meant to be tracked.
- [ ] Pre-commit hook rejecting journal file types
- [ ] Disable editor/library telemetry

---

## Phase 1 — Ingestion

Not started. Blocked on one question: **where do the journals live and how are they
organized** — one file per trip, per year, or one large file? That determines the
chunking strategy more than any other factor.
