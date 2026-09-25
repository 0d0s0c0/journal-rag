"""Search the journal index. Retrieval only — no LLM writes anything here.

    uv run python -m src.search "best places I snorkelled"
    uv run python -m src.search "Emily" --mode fts
    uv run python -m src.search "outstanding meals" --year 2019
    uv run python -m src.search "rain" --no-text          # metadata only

Two retrievers, fused. They fail in opposite directions, which is the whole
reason for running both:

  vector   finds meaning. "the restaurant where I accidentally underpaid"
           reaches the right entry on wording the journal never uses. It is
           useless on names: "Emily" appears in 3 chunks of 4,749 and vector
           search finds one of them.

  BM25     finds rare words. "Emily" ranks all three first. It cannot match a
           paraphrase at all — no shared terms, no score.

Fused with Reciprocal Rank Fusion, which combines by POSITION rather than
score. A cosine of 0.78 and a BM25 score of 10.8 are not comparable numbers;
their ranks are.

This exists before any generation step on purpose. If the right chunk is not in
the top few, no model can rescue the answer — it will write fluent prose about
the wrong entries. Looking at raw retrieval is the only way to tell a retrieval
problem from a generation one.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request

import lancedb

from src.config import CONFIG

MODEL = CONFIG.embed.model
HOST = CONFIG.ollama_host
TABLE = "chunks"

QUERY_INSTRUCTION = CONFIG.embed.query_instruction
YEAR_IN_QUERY = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")

# RRF constant from the original paper. Not sensitive: it damps the advantage of
# rank 1 over rank 2 so one confident ranker cannot dominate the other outright.
RRF_K = 60


def embed_query(text: str) -> list[float]:
    req = urllib.request.Request(
        f"{HOST}/api/embed",
        data=json.dumps({"model": MODEL,
                         "input": CONFIG.embed.query_prompt(text)}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["embeddings"][0]


def _table():
    return lancedb.connect(str(CONFIG.paths.index)).open_table(TABLE)


def _filtered(q, year: int | None, trip: str | None):
    # Filters run before ranking, so "in 2019" genuinely narrows the candidates
    # rather than relying on the embedding to encode a year, which it does not.
    if year:
        q = q.where(f"year = {year}")
    if trip:
        q = q.where(f"trip = '{trip}'")
    return q


def vector_search(query: str, k: int, year=None, trip=None) -> list[dict]:
    return _filtered(_table().search(embed_query(query)).limit(k), year, trip).to_list()


def fts_search(query: str, k: int, year=None, trip=None) -> list[dict]:
    """Keyword search. Empty if there is no FTS index or the query has no terms."""
    try:
        q = _table().search(query, query_type="fts").limit(k)
        return _filtered(q, year, trip).to_list()
    except Exception:
        # A typo, or a query that tokenises to nothing, must not turn a working
        # search into zero results — the vector side still stands.
        return []


def reciprocal_rank_fusion(runs: list[tuple[list[dict], float]], k: int) -> list[dict]:
    """Fuse ranked lists by position.

        score(doc) = sum over retrievers of  weight / (RRF_K + rank)

    Using rank rather than score is what makes this safe. The two retrievers
    produce numbers on unrelated scales; normalising them would mean inventing
    a conversion that does not exist.
    """
    scores: dict[str, float] = {}
    rows: dict[str, dict] = {}
    for hits, weight in runs:
        for rank, h in enumerate(hits, start=1):
            cid = h["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + weight / (RRF_K + rank)
            # Prefer the copy carrying a vector distance, so similarity can
            # still be shown for anything the vector side also found.
            if cid not in rows or (h.get("_distance") is not None
                                   and rows[cid].get("_distance") is None):
                rows[cid] = h
    out = []
    for cid in sorted(scores, key=lambda c: -scores[c])[:k]:
        row = dict(rows[cid])
        row["_rrf"] = scores[cid]
        out.append(row)
    return out


def search(query: str, k: int = CONFIG.retrieval.top_k, year: int | None = None,
           trip: str | None = None, mode: str | None = None) -> list[dict]:
    mode = mode or CONFIG.retrieval.mode
    if mode == "vector":
        return vector_search(query, k, year, trip)
    if mode == "fts":
        return fts_search(query, k, year, trip)

    # Fetch deeper than k from each retriever so fusion has room to work: a
    # result ranked 8th by one and 2nd by the other should still surface.
    depth = max(k * 4, 20)
    return reciprocal_rank_fusion([
        (vector_search(query, depth, year, trip), CONFIG.retrieval.vector_weight),
        (fts_search(query, depth, year, trip), CONFIG.retrieval.fts_weight),
    ], k)


def similarity(hit: dict) -> float | None:
    """L2 distance -> cosine for unit-norm vectors. None for FTS-only hits."""
    d = hit.get("_distance")
    return None if d is None else 1 - (d * d) / 2


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("query")
    ap.add_argument("-k", type=int, default=CONFIG.retrieval.top_k)
    ap.add_argument("--mode", choices=["hybrid", "vector", "fts"],
                    default=CONFIG.retrieval.mode)
    ap.add_argument("--year", type=int, help="restrict to a year")
    ap.add_argument("--trip", help="restrict to a trip label")
    ap.add_argument("--chars", type=int, default=320, help="snippet length")
    ap.add_argument("--no-text", action="store_true", help="metadata and scores only")
    args = ap.parse_args()

    year = args.year
    if year is None and (m := YEAR_IN_QUERY.search(args.query)):
        year = int(m.group(1))
        print(f"(detected year {year} in the query — filtering. "
              f"Use --year 0 to disable.)\n")
    if year == 0:
        year = None

    hits = search(args.query, k=args.k, year=year, trip=args.trip, mode=args.mode)
    if not hits:
        print("no results")
        return

    for i, h in enumerate(hits, 1):
        sim = similarity(h)
        score = (f"{sim:.3f}" if sim is not None
                 else f"rrf {h['_rrf']:.4f}" if "_rrf" in h
                 else f"{h.get('_score', 0):.2f}")
        photos = f"  {h['n_photos']} photo(s)" if h["n_photos"] else ""
        part = f"  [{h['chunk_index'] + 1}/{h['n_chunks']}]" if h["n_chunks"] > 1 else ""
        recon = "  [RECONSTRUCTED from photo captions]" if h.get("reconstructed") else ""
        print(f"{i}. {score}  {h['entry_date']}  {h['trip']}{part}{photos}{recon}")
        print(f"   {h['chunk_id']}")
        if not args.no_text:
            body = h["text"].split(" — ", 1)[-1].replace("\n", " ")
            print(f"   {body[:args.chars]}{'…' if len(body) > args.chars else ''}")
        print()


if __name__ == "__main__":
    main()
