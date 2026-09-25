"""Split entries into retrievable chunks.

    uv run python -m src.chunk               # entries.jsonl -> chunks.jsonl
    uv run python -m src.chunk --dry-run
    uv run python -m src.chunk --max 1500    # try a different size

A chunk is the unit of retrieval: the text that gets its own vector, and the
text that comes back when a search matches. No model is involved here — this is
deterministic splitting and metadata assembly.

Three decisions, each grounded in what the archive actually looks like:

  * The entry is the natural unit. It is one day, one place, one narrative, and
    59% of entries already fit under the size limit with nothing to decide.

  * Long entries split on PARAGRAPH boundaries, not character counts. The 15,208
    character Syldavia entry holds lunch, a tuk-tuk driver, two temples, a
    circus and dinner; its paragraph breaks fall exactly between them. Splitting
    every N characters would cut mid-meal and produce chunks about nothing.

  * Every chunk is prefixed with its date and trip. A fragment reading
    "Everything felt like it was cooked in microwave" is meaningless alone and
    retrieves for nothing; "2019-02-13, klow — Everything felt like..." is
    self-contained and findable by place and date.

Oversized single paragraphs are split on sentence boundaries as a last resort,
with overlap so a thought spanning the seam survives in both halves.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path

from src.config import CONFIG

# Chosen against the measured distribution: median entry is 1,574 characters and
# 41% exceed 2,000. A 2,000 limit leaves the majority whole while splitting the
# long tail. Tunable with --max; Phase 3 measures whether it was right.
MAX_CHARS = CONFIG.chunking.max_chars
MIN_CHARS = CONFIG.chunking.min_chars      # below this, fold into a neighbour
OVERLAP_SENTENCES = CONFIG.chunking.overlap_sentences

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Chunk:
    chunk_id: str
    entry_id: str
    text: str                    # what gets embedded: header + body
    header_len: int              # body is text[header_len:] — stored rather than
                                 # duplicated, which was 37% of the file
    entry_date: str
    year: int
    month: int
    trip: str | None
    source_file: str
    chunk_index: int
    n_chunks: int
    chars: int
    # Photos belong to the entry. Repeating the list on every chunk of a
    # multi-chunk entry stored 40,188 paths for 14,305 actual ones; look them
    # up from entries.jsonl by entry_id instead. The count is kept so chunks
    # can be filtered without the join.
    n_photos: int = 0
    warnings: list[str] = field(default_factory=list)


def split_paragraphs(text: str) -> list[str]:
    """Paragraphs, as written. Blank lines separate; single newlines do not."""
    parts = re.split(r"\n\s*\n+", text)
    return [p.strip() for p in parts if p.strip()]


def split_sentences(text: str, limit: int) -> list[str]:
    """Last resort for a single paragraph over the limit.

    Sentence boundaries are the least-bad seam inside a paragraph. One sentence
    of overlap is carried forward so a thought spanning the split is not lost to
    both halves.
    """
    sentences = [s for s in SENTENCE_END.split(text) if s.strip()]
    out: list[str] = []
    cur: list[str] = []
    n = 0
    for s in sentences:
        if cur and n + len(s) > limit:
            out.append(" ".join(cur))
            cur = cur[-OVERLAP_SENTENCES:] if OVERLAP_SENTENCES else []
            n = sum(len(x) + 1 for x in cur)
        cur.append(s)
        n += len(s) + 1
    if cur:
        out.append(" ".join(cur))
    return out or [text]


def pack(paragraphs: list[str], limit: int) -> list[str]:
    """Group paragraphs into chunks at or under the limit.

    Paragraphs are never split unless one alone exceeds the limit. Consecutive
    short paragraphs are packed together so a two-line paragraph does not become
    its own near-empty chunk.
    """
    chunks: list[str] = []
    cur: list[str] = []
    n = 0
    for p in paragraphs:
        if len(p) > limit:
            if cur:
                chunks.append("\n\n".join(cur))
                cur, n = [], 0
            chunks.extend(split_sentences(p, limit))
            continue
        if cur and n + len(p) + 2 > limit:
            chunks.append("\n\n".join(cur))
            cur, n = [], 0
        cur.append(p)
        n += len(p) + 2
    if cur:
        chunks.append("\n\n".join(cur))

    # A trailing scrap reads as its own chunk about nothing; fold it back.
    if len(chunks) > 1 and len(chunks[-1]) < MIN_CHARS:
        merged = chunks[-2] + "\n\n" + chunks[-1]
        chunks = chunks[:-2] + [merged]
    return chunks


def header(entry: dict) -> str:
    """Context stamped onto every chunk so it stands alone when retrieved."""
    trip = (entry.get("trip") or "").strip()
    return f"{entry['entry_date']}, {trip} — " if trip else f"{entry['entry_date']} — "


def chunk_entry(entry: dict, limit: int = MAX_CHARS) -> list[Chunk]:
    paragraphs = split_paragraphs(entry["text"])
    if not paragraphs:
        return []

    hdr = header(entry)
    # The limit is the size of the finished chunk, header included — that is
    # what the embedding model and the context budget actually see.
    bodies = pack(paragraphs, max(limit - len(hdr), 200))
    photos = [p["path"] for p in entry.get("photos", [])]
    date = entry["entry_date"]

    out: list[Chunk] = []
    for i, body in enumerate(bodies):
        # Only chunk-level warnings here; the entry's own warnings stay in
        # entries.jsonl rather than being copied onto each of its chunks.
        # Provenance must survive into the chunk: a reconstructed entry is
        # photograph captions, not something that was written, and a search
        # result has to say so.
        warnings: list[str] = [w for w in entry.get("warnings", [])
                               if w in ("reconstructed", "no_captions")]
        if len(bodies) > 1:
            warnings.append("split")
        if len(hdr) + len(body) > limit:
            warnings.append("oversize")
        out.append(Chunk(
            chunk_id=f"{entry['id']}/{i}",
            entry_id=entry["id"],
            text=hdr + body,
            header_len=len(hdr),
            entry_date=date,
            year=int(date[:4]),
            month=int(date[5:7]),
            trip=entry.get("trip"),
            source_file=entry["source_file"],
            chunk_index=i,
            n_chunks=len(bodies),
            chars=len(hdr) + len(body),
            n_photos=len(photos),
            warnings=warnings,
        ))
    return out


def main() -> None:
    args = sys.argv[1:]
    dry = "--dry-run" in args
    limit = MAX_CHARS
    if "--max" in args:
        limit = int(args[args.index("--max") + 1])

    src = CONFIG.paths.entries
    if not src.exists():
        sys.exit(f"missing {src} — run src.convert first")

    entries = [json.loads(l) for l in src.open()]

    # Entries rebuilt from photographs live in their own file so they can never
    # be confused with written prose. They are chunked alongside, carrying the
    # `reconstructed` flag through to every chunk.
    recon = src.parent / "entries-reconstructed.jsonl"
    n_recon = 0
    if recon.exists():
        extra = [json.loads(l) for l in recon.open()]
        entries += extra
        n_recon = len(extra)

    chunks: list[Chunk] = []
    for e in entries:
        chunks.extend(chunk_entry(e, limit))

    sizes = sorted(c.chars for c in chunks)
    n = len(sizes)
    split_entries = len({c.entry_id for c in chunks if c.n_chunks > 1})
    over = sum(1 for s in sizes if s > limit)

    print(f"limit              : {limit} chars")
    print(f"entries            : {len(entries):,}"
          + (f"  ({n_recon} reconstructed from photographs)" if n_recon else ""))
    print(f"chunks             : {n:,}  ({n / len(entries):.2f} per entry)")
    print(f"entries split       : {split_entries:,} "
          f"({100 * split_entries // len(entries)}%)")
    print(f"chunk chars        : min={sizes[0]} median={sizes[n // 2]} "
          f"p90={sizes[int(n * 0.9)]} max={sizes[-1]}")
    print(f"over the limit     : {over}  (single paragraphs that resisted splitting)")
    print(f"under {MIN_CHARS} chars     : {sum(1 for s in sizes if s < MIN_CHARS)}")
    print(f"total chars        : {sum(sizes):,}  (~{sum(sizes) // 4:,} tokens)")
    print(f"with photos        : {sum(1 for c in chunks if c.n_photos):,}")

    if dry:
        print("\n--dry-run: nothing written")
        return

    out = CONFIG.paths.chunks
    with out.open("w") as fh:
        for c in chunks:
            fh.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
