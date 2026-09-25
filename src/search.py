"""Search the journal index. Retrieval only — no LLM involved.

    uv run python -m src.search "best places I snorkelled"
    uv run python -m src.search "outstanding meals" --year 2019
    uv run python -m src.search "temples" -k 10
    uv run python -m src.search "rain" --no-text        # metadata only

This exists before any generation step on purpose. If the right chunk is not in
the top few results, no model can rescue the answer — it will simply write
fluent prose about the wrong entries. Looking at raw retrieval is the only way
to tell the difference between a retrieval problem and a generation one, and
almost every RAG project skips it and then blames the model.

Queries carry the instruction prefix; documents were embedded bare. Measured in
Phase 0: with the prefix the spread between relevant and irrelevant passages
went from 0.05 (unusable) to 0.20.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path

import lancedb

from src.config import CONFIG

MODEL = CONFIG.embed.model
HOST = CONFIG.ollama_host
TABLE = "chunks"

# Instruction-aware embedding model. This belongs on queries ONLY.
QUERY_INSTRUCTION = CONFIG.embed.query_instruction

YEAR_IN_QUERY = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")


def embed_query(text: str) -> list[float]:
    prompt = CONFIG.embed.query_prompt(text)
    req = urllib.request.Request(
        f"{HOST}/api/embed",
        data=json.dumps({"model": MODEL, "input": prompt}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["embeddings"][0]


def search(query: str, k: int = CONFIG.retrieval.top_k, year: int | None = None,
           trip: str | None = None) -> list[dict]:
    db = lancedb.connect(str(CONFIG.paths.index))
    q = db.open_table(TABLE).search(embed_query(query)).limit(k)
    # Metadata filters run before the vector comparison, so "in 2019" narrows
    # the candidates rather than relying on the embedding to encode a year,
    # which it does not.
    if year:
        q = q.where(f"year = {year}")
    if trip:
        q = q.where(f"trip = '{trip}'")
    return q.to_list()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("query")
    ap.add_argument("-k", type=int, default=CONFIG.retrieval.top_k, help="results to return")
    ap.add_argument("--year", type=int, help="restrict to a year")
    ap.add_argument("--trip", help="restrict to a trip label")
    ap.add_argument("--chars", type=int, default=320, help="snippet length")
    ap.add_argument("--no-text", action="store_true",
                    help="metadata and scores only")
    args = ap.parse_args()

    year = args.year
    if year is None:
        m = YEAR_IN_QUERY.search(args.query)
        if m:
            year = int(m.group(1))
            print(f"(detected year {year} in the query — filtering. "
                  f"Use --year 0 to disable.)\n")
    if year == 0:
        year = None

    hits = search(args.query, k=args.k, year=year, trip=args.trip)
    if not hits:
        print("no results")
        return

    for i, h in enumerate(hits, 1):
        # LanceDB returns L2 distance; these vectors are unit-norm, so
        # cosine similarity = 1 - d^2/2.
        d = h.get("_distance")
        sim = 1 - (d * d) / 2 if d is not None else float("nan")
        photos = f"  {h['n_photos']} photo(s)" if h["n_photos"] else ""
        recon = "  [RECONSTRUCTED from photo captions]" if h.get("reconstructed") else ""
        part = (f"  [{h['chunk_index'] + 1}/{h['n_chunks']}]"
                if h["n_chunks"] > 1 else "")
        print(f"{i}. {sim:.3f}  {h['entry_date']}  {h['trip']}{part}{photos}{recon}")
        print(f"   {h['chunk_id']}")
        if not args.no_text:
            body = h["text"].split(" — ", 1)[-1].replace("\n", " ")
            print(f"   {body[:args.chars]}{'…' if len(body) > args.chars else ''}")
        print()


if __name__ == "__main__":
    main()
