"""Embed chunks into a local LanceDB index.

    uv run python -m src.embed              # build / update the index
    uv run python -m src.embed --rebuild    # discard and start over
    uv run python -m src.embed --verify     # check an existing index, embed nothing

Measured on this machine: ~10 chunks/s in batches, so the full archive takes
about five minutes. That matters more than it sounds — re-embedding is cheap
enough to iterate on chunk size freely rather than having to get it right first.

Two rules that are easy to get wrong and fail silently:

  * Documents are embedded BARE. The instruction prefix belongs on queries only.
    Applying it to documents degrades the whole index with no error, and the
    only symptom is that retrieval is mediocre.

  * Vectors from different models are not comparable. Swapping the embedding
    model does not degrade the index, it invalidates it. The model name is part
    of each row's hash so a mismatch is detected rather than silently returning
    nonsense.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import lancedb
import pyarrow as pa

MODEL = "qwen3-embedding:0.6b"
DIM = 1024
HOST = "http://localhost:11434"
BATCH = 32                    # throughput flattens above ~16; 32 is comfortable
TABLE = "chunks"


def table_names(db) -> list[str]:
    """Names of existing tables.

    lancedb moved from `table_names()` (a list, now deprecated) to
    `list_tables()` (a response object with a `.tables` attribute). Swapping the
    call without the attribute makes every existence check silently False, which
    re-embeds the whole archive and then fails to create a table that is already
    there. Handle both.
    """
    lt = db.list_tables()
    return list(getattr(lt, "tables", lt))


def content_hash(text: str, model: str) -> str:
    """Identity of an embedded row: its text and the model that produced it."""
    return hashlib.sha256(f"{model}\x00{text}".encode()).hexdigest()[:16]


def embed_batch(texts: list[str], model: str = MODEL, retries: int = 3) -> list[list[float]]:
    """Embed a batch. Documents go in bare — no instruction prefix."""
    payload = json.dumps({"model": model, "input": texts}).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                f"{HOST}/api/embed", data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=180) as r:
                out = json.loads(r.read())["embeddings"]
            if len(out) != len(texts):
                raise ValueError(f"asked for {len(texts)} vectors, got {len(out)}")
            return out
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            if attempt == retries - 1:
                raise
            print(f"    retry {attempt + 1}/{retries - 1} after {type(e).__name__}")
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


SCHEMA = pa.schema([
    pa.field("chunk_id", pa.string()),
    pa.field("entry_id", pa.string()),
    pa.field("hash", pa.string()),
    pa.field("vector", pa.list_(pa.float32(), DIM)),
    pa.field("text", pa.string()),
    pa.field("entry_date", pa.string()),
    pa.field("year", pa.int32()),
    pa.field("month", pa.int32()),
    pa.field("trip", pa.string()),
    pa.field("source_file", pa.string()),
    pa.field("chunk_index", pa.int32()),
    pa.field("n_chunks", pa.int32()),
    pa.field("n_photos", pa.int32()),
])


def verify(table) -> int:
    """Check an index rather than trusting it. Returns the number of problems."""
    df = table.to_pandas()
    problems = 0

    def check(ok: bool, msg: str) -> None:
        nonlocal problems
        print(f"  {'ok  ' if ok else 'FAIL'}  {msg}")
        problems += 0 if ok else 1

    check(len(df) > 0, f"rows present: {len(df):,}")
    check(df["chunk_id"].is_unique, "chunk_ids unique")
    dims = {len(v) for v in df["vector"].head(500)}
    check(dims == {DIM}, f"vector dimensions: {dims or 'none'}")

    import numpy as np
    sample = np.vstack(df["vector"].head(1000).to_numpy())
    check(not np.isnan(sample).any(), "no NaN values")
    norms = np.linalg.norm(sample, axis=1)
    check(bool((norms > 0).all()), f"no zero vectors (min norm {norms.min():.3f})")
    check(df["entry_date"].notna().all(), "every row has a date")
    return problems


def main() -> None:
    args = sys.argv[1:]
    rebuild = "--rebuild" in args
    verify_only = "--verify" in args

    root = Path.home() / "playground/journal-data"
    src = root / "text/chunks.jsonl"
    if not src.exists():
        sys.exit(f"missing {src} — run src.chunk first")

    db = lancedb.connect(str(root / "index"))

    if verify_only:
        if TABLE not in table_names(db):
            sys.exit("no index yet — run without --verify first")
        print("verifying index:")
        sys.exit(1 if verify(db.open_table(TABLE)) else 0)

    chunks = [json.loads(l) for l in src.open()]
    for c in chunks:
        c["hash"] = content_hash(c["text"], MODEL)

    if rebuild and TABLE in table_names(db):
        db.drop_table(TABLE)
        print("dropped existing table")

    existing: set[str] = set()
    if TABLE in table_names(db):
        existing = set(db.open_table(TABLE).to_pandas()["hash"])

    todo = [c for c in chunks if c["hash"] not in existing]
    print(f"chunks       : {len(chunks):,}")
    print(f"already done : {len(chunks) - len(todo):,}")
    print(f"to embed     : {len(todo):,}")
    if not todo:
        print("\nnothing to do; verifying instead:")
        sys.exit(1 if verify(db.open_table(TABLE)) else 0)

    rows: list[dict] = []
    started = time.perf_counter()
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        vectors = embed_batch([c["text"] for c in batch])
        for c, v in zip(batch, vectors):
            rows.append({
                "chunk_id": c["chunk_id"], "entry_id": c["entry_id"], "hash": c["hash"],
                "vector": v, "text": c["text"], "entry_date": c["entry_date"],
                "year": c["year"], "month": c["month"], "trip": c["trip"] or "",
                "source_file": c["source_file"], "chunk_index": c["chunk_index"],
                "n_chunks": c["n_chunks"], "n_photos": c["n_photos"],
            })
        done = i + len(batch)
        elapsed = time.perf_counter() - started
        rate = done / elapsed
        print(f"  {done:>5,}/{len(todo):,}  {rate:5.1f}/s  "
              f"eta {(len(todo) - done) / rate / 60:4.1f} min", end="\r", flush=True)

    print(f"\nembedded {len(rows):,} in {(time.perf_counter() - started) / 60:.1f} min")

    if TABLE in table_names(db):
        # Chunk ids are stable, so a re-embedded chunk replaces its old row
        # rather than appearing twice.
        table = db.open_table(TABLE)
        ids = "', '".join(r["chunk_id"] for r in rows)
        table.delete(f"chunk_id IN ('{ids}')")
        table.add(rows)
    else:
        table = db.create_table(TABLE, rows, schema=SCHEMA)

    print(f"\nindex at {root / 'index'}")
    print("verifying:")
    sys.exit(1 if verify(table) else 0)


if __name__ == "__main__":
    main()
