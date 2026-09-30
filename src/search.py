"""Search the journal index. Retrieval only — no LLM writes anything here.

    uv run python -m src.search "best places I snorkelled"
    uv run python -m src.search "Margarethe" --mode fts
    uv run python -m src.search "outstanding meals" --year 2019
    uv run python -m src.search "rain" --no-text          # metadata only
    uv run python -m src.search "Margarethe" --all        # EVERY entry, not top-k

Two retrievers, fused. They fail in opposite directions, which is the whole
reason for running both:

  vector   finds meaning. "the inn where I accidentally left a telegram behind"
           reaches the right entry on wording the journal never uses. It is
           useless on names: one surname appears in 4 chunks of 4,042 and vector
           search finds one of them — at k=5 and still at k=25, so this is not a
           matter of looking deeper.

  BM25     finds rare words. It ranks all four of those first. It cannot match a
           paraphrase at all — no shared terms, no score.

Measured on the eval set, neither mode wins outright and no RRF weighting gets
both — the tradeoff is monotone:

    weights (vec:fts)   recall@1   recall@10   the 4-chunk name query
    1:0  vector only      0.77       0.85            1 of 4
    1:1  hybrid           0.54       0.92            3 of 4
    0:1  fts only         0.46       0.69            4 of 4

So `vector` stays the default for questions, and `--all` exists for the case
neither serves: "which entries mention X" is not a ranked question at all.

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

# Everything except `vector`. A full scan that selects * also materialises 1,024
# floats per row, which is ~16 MB of embeddings the caller never looks at.
COLUMNS = ["chunk_id", "chunk_index", "entry_date", "entry_id", "month",
           "n_chunks", "n_photos", "reconstructed", "source_file", "text",
           "trip", "year"]

QUERY_INSTRUCTION = CONFIG.embed.query_instruction
YEAR_IN_QUERY = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")

STOP = frozenset("""a an the and or of in on at to for with i we my our it that this
was were is are be been had have has did do does from by as but so then there here
when where what which who how why all any some more most other into over under
about after before during while than too very just only also not no nor""".split())

# RRF constant from the original paper. Not sensitive: it damps the advantage of
# rank 1 over rank 2 so one confident ranker cannot dominate the other outright.
RRF_K = 60


def detect_year(query: str) -> int | None:
    """The year named in a question, if any.

    Worth filtering on rather than trusting the embedding, because the embedding
    does not encode dates at all. Measured on "where did I snorkel in 2022",
    against 16 entries that actually match:

        no filter, k=5    2 of 5 excerpts were even from 2022   12% coverage
        year=2022, k=5    5 of 5                                25%
        year=2022, k=25                                         88%

    Three of five excerpts were spent on the wrong years, and "2022" in the
    prompt does nothing to stop the model answering from them. A filter runs
    before ranking, so it is exact and it costs nothing.
    """
    m = YEAR_IN_QUERY.search(query)
    return int(m.group(1)) if m else None


# A term occurring in at most this many entries is discriminative enough that a
# literal match is almost certainly what the asker meant. Absolute rather than a
# percentage: what matters is "few enough to read", not a share of the corpus.
RARE_ENTRY_MAX = 10


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _vectors_for(chunk_ids) -> dict[str, list]:
    """Embeddings for a handful of known chunk ids.

    Fetched by id and scored in Python rather than run through the vector index
    with a filter. The candidate set is tiny — bounded by RARE_ENTRY_MAX per term
    — and an ANN search that post-filters can return nothing at all when the
    filter is this narrow. Exactness matters more than speed at this size.
    """
    ids = [c.replace("'", "''") for c in chunk_ids]
    if not ids:
        return {}
    quoted = ",".join(f"'{c}'" for c in ids)
    rows = (_table().search().select(["chunk_id", "vector"])
            .where(f"chunk_id IN ({quoted})").limit(None).to_list())
    return {r["chunk_id"]: r["vector"] for r in rows}


def rare_term_hits(query: str, limit: int, year: int | None = None,
                   trip: str | None = None, max_terms: int = 4,
                   qv: list[float] | None = None) -> list[dict]:
    """Chunks containing a RARE literal term from the query.

    A safety net under dense retrieval, for its one catastrophic failure mode.
    Asked "the time i won a tombola", vector search did not return the entry
    containing the word *tombola* **in its top 100** — one of only three entries
    in the archive that contain it. A single rare word inside a 1,600-character
    chunk barely moves the embedding, so the chunk ranks on everything else it is
    about. Adding three words of context fixed it by giving the embedding
    something to work with, which is not a thing a person should have to know in
    order to search their own diary.

    Not solvable by switching retriever. Measured on that query: BM25 ranks it 4th
    and hybrid 9th, so hybrid at the default k=5 still misses it — while on the
    eval set BM25 alone costs recall@1 0.77 -> 0.46. Reserving a couple of slots
    for literal matches keeps dense retrieval's ranking and removes its blind
    spot, instead of trading one failure for another.

    Rarity is the whole point of the threshold: guaranteeing slots for a common
    word would spend them on nothing in particular.
    """
    terms = [t for t in re.findall(r"[\w']+", query.lower())
             if t not in STOP and len(t) > 3 and not YEAR_IN_QUERY.fullmatch(t)]

    rare: dict[str, list[dict]] = {}
    for t in terms[:max_terms]:
        rows = term_lookup(t, year=year, trip=trip, substring=True)
        if rows and len({r["entry_id"] for r in rows}) <= RARE_ENTRY_MAX:
            rare[t] = rows
    if not rare:
        return []

    # Rank by HOW MANY of the query's rare terms a chunk contains, before how
    # often. Taking them in query order instead was measurably wrong: for
    # "monasteries i stayed at on Mt Vespugia" both "monasteries" (8 entries)
    # and the place name (8) qualify, the first encountered took both slots, one
    # went to an entry about monasteries in an entirely different country. That
    # displaced two ranked results and turned an answer the model had been giving
    # 4 times out of 4 into a refusal 4 times out of 4.
    #
    # Matching two rare terms at once is much stronger evidence than matching one
    # of them twice, so term coverage outranks frequency.
    by_chunk: dict[str, dict] = {}
    for t, rows in rare.items():
        for r in rows:
            e = by_chunk.setdefault(r["chunk_id"],
                                    {"row": r, "terms": set(), "count": 0})
            e["terms"].add(t)
            e["count"] += r["_count"]

    # Rank the candidates by SIMILARITY to the query, not by how often they say
    # the word. Mention count is a useless tiebreak for a noun: asked about a
    # tiffin, all nine entries containing that word mentioned it exactly once,
    # so the slots went to the first two by date and the right entry — sixth in
    # that arbitrary order — got nothing.
    #
    # The two signals are weak alone and decisive together. That entry scores
    # 0.487 against the query and ranks 9th of 4,042 corpus-wide, which is why
    # dense retrieval alone missed it; among the nine that contain the word
    # literally it is first by a clear margin (0.487 vs 0.422). The lexical side
    # narrows, the vector side chooses.
    vecs = _vectors_for(by_chunk) if qv is not None else {}
    for e in by_chunk.values():
        v = vecs.get(e["row"]["chunk_id"])
        e["sim"] = _cosine(qv, v) if v is not None else 0.0

    def emit(e: dict) -> dict:
        row = dict(e["row"])
        row["_rare_terms"] = sorted(e["terms"])
        row["_count"] = e["count"]
        row["_rare_sim"] = e["sim"]
        return row

    # Term coverage still outranks similarity: a chunk containing TWO of the
    # query's rare terms is stronger evidence than one that is merely a closer
    # embedding. That ordering is what stopped an earlier bug where slots meant
    # for one rare term were spent on an unrelated entry matching the other.
    #
    # One chunk per ENTRY. These slots exist to surface entries the ranker
    # missed, and two chunks of the same entry surface one entry — ranking by
    # similarity alone did exactly that, spending both slots on a single day.
    out: list[dict] = []
    entries: set[str] = set()
    for e in sorted(by_chunk.values(), key=lambda e: (-len(e["terms"]), -e["sim"])):
        eid = e["row"]["entry_id"]
        if eid in entries:
            continue
        entries.add(eid)
        out.append(emit(e))
        if len(out) >= limit:
            break
    return out


def literal_coverage(query: str, year: int | None = None, trip: str | None = None,
                     max_terms: int = 4) -> tuple[str, int] | None:
    """The query's most discriminative content term, and how many entries hold it.

    Exists so a partial answer can say that it is partial. "where did I snorkel in
    2022" has 16 matching entries; at the default k=5 the model sees five excerpts
    and writes a confident list of four places with no indication that three
    quarters of the answer is missing. Silence there is the problem — the answer
    is not wrong, it is incomplete, and those look identical in the output.

    Substring matching on purpose: whole-word "snorkel" misses "snorkeling" and
    "snorkelled", which is most of the real occurrences.

    Returns the term with the FEWEST non-zero matches, on the reasoning that the
    rarest term is the one the question is really about — "meals" is a weaker
    signal than "snorkel" in a corpus of travel journals.

    Where that reasoning breaks: "what were my favourite meals" picks
    *favourite* (1 entry) over *meals*, because people rarely write the word
    "favourite" in a journal. The caller only warns when the count EXCEEDS the
    excerpts it used, so this misfire is silent rather than wrong — it misses a
    warning it could have given. Left deliberately crude: tuning a
    selectivity threshold against two examples is how the place-checking rule in
    ask.py first went wrong, and a false negative here costs a hint, not an
    answer.
    """
    terms = [t for t in re.findall(r"[\w']+", query.lower())
             if t not in STOP and len(t) > 3 and not YEAR_IN_QUERY.fullmatch(t)]
    if not terms:
        return None

    best: tuple[str, int] | None = None
    for t in terms[:max_terms]:
        rows = term_lookup(t, year=year, trip=trip, substring=True)
        n = len({r["entry_id"] for r in rows})
        if n and (best is None or n < best[1]):
            best = (t, n)
    return best


def worth_a_literal_lookup(query: str) -> bool:
    """Is this query short enough that a literal term lookup is worth trying?

    A cheap gate, not a classifier — it only decides whether to spend one scan
    finding out. The authoritative test is whether that scan matches anything,
    which is why the caller checks the count before saying a word. Two content
    words are allowed because a short phrase ("glass orangery") is a perfectly good
    literal lookup; the cost of a false positive is a scan whose result is zero.

    The failure it exists to catch: dense retrieval needs context to embed. "the
    inn where I accidentally left a telegram behind" gives it plenty; a surname on its
    own gives it almost none, and the result is a low, flat ranking of unrelated
    entries — measured here, a top hit of 0.470 with ranks 2-5 between 0.356 and
    0.394, none of them containing the term at all.

    Deliberately keyed on the SHAPE of the query, not on the scores it produced.
    An earlier attempt in this project to read meaning into flat score
    distributions did not survive contact with the data: a flat spread turned out
    to be equally consistent with a real answer. Counting content words is crude
    but it is not wrong about what it measures.
    """
    terms = [t for t in re.findall(r"[\w']+", query.lower())
             if t not in STOP and len(t) > 2]
    return 0 < len(terms) <= 2


def embed_query(text: str) -> list[float]:
    req = urllib.request.Request(
        f"{HOST}/api/embed",
        data=json.dumps({"model": MODEL,
                         "input": CONFIG.embed.query_prompt(text),
                         "options": {"num_ctx": CONFIG.embed.num_ctx}}).encode(),
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


def vector_search(query: str, k: int, year=None, trip=None,
                  qv: list[float] | None = None) -> list[dict]:
    qv = embed_query(query) if qv is None else qv
    return _filtered(_table().search(qv).limit(k), year, trip).to_list()


def fts_search(query: str, k: int, year=None, trip=None) -> list[dict]:
    """Keyword search. Empty if there is no FTS index or the query has no terms."""
    try:
        q = _table().search(query, query_type="fts").limit(k)
        return _filtered(q, year, trip).to_list()
    except Exception:
        # A typo, or a query that tokenises to nothing, must not turn a working
        # search into zero results — the vector side still stands.
        return []


def term_lookup(term: str, year: int | None = None, trip: str | None = None,
                substring: bool = False) -> list[dict]:
    """EVERY chunk containing `term`. Exhaustive, unranked, no top-k.

    "Which entries mention X" is not a ranked-retrieval question, and no ranked
    retriever can answer it: top-k returns k rows whether the term occurs twice
    or forty times. Asking for a complete answer from a tool whose contract is
    "the best k" will always quietly under-report.

    Vector search is the worst choice here. Measured on this archive, one surname
    occurs in 4 chunks and vector search surfaces **1 of them even at k=25** —
    the four sit in four unrelated trips, so each embedding is dominated by
    whatever else that entry is about, and a bare proper noun carries almost no
    semantic signal to compete with it. BM25 ranks all four first; but that is
    luck of the corpus size, not a guarantee, and it still caps at k.

    Two-stage match, because neither stage is sufficient alone:

      SQL LIKE    pushed down to the scan, so it is cheap, but it is a SUBSTRING
                  test and over-matches — '%olomon%' also hit an unrelated word
                  containing those letters.
      whole word  a regex pass in Python drops those. Substring behaviour is
                  still available via `substring=True`, since a stem search
                  ("monaster") is a legitimate thing to want.
    """
    needle = term.strip().lower()
    if not needle:
        return []

    # LIKE wildcards inside the term would silently widen the search; a quote
    # would break the predicate. Escape both.
    esc = needle.replace("'", "''").replace("%", r"\%").replace("_", r"\_")
    where = [f"lower(text) LIKE '%{esc}%' ESCAPE '\\'"]
    if year:
        where.append(f"year = {year}")
    if trip:
        where.append(f"trip = '{trip}'")

    q = _table().search().select(COLUMNS).where(" AND ".join(where))
    # `limit` has a non-None default, so an exhaustive scan must say so
    # explicitly or it silently returns the first handful.
    rows = q.limit(None).to_list()

    if not substring:
        pat = re.compile(rf"\b{re.escape(needle)}\b", re.IGNORECASE)
        rows = [r for r in rows if pat.search(r["text"])]

    for r in rows:
        r["_count"] = len(re.findall(
            re.escape(needle) if substring else rf"\b{re.escape(needle)}\b",
            r["text"], re.IGNORECASE))
    rows.sort(key=lambda r: (r["entry_date"], r["chunk_index"]))
    return rows


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


def _ranked(query: str, k: int, year, trip, mode: str,
            qv: list[float] | None = None) -> list[dict]:
    if mode == "vector":
        return vector_search(query, k, year, trip, qv)
    if mode == "fts":
        return fts_search(query, k, year, trip)

    # Fetch deeper than k from each retriever so fusion has room to work: a
    # result ranked 8th by one and 2nd by the other should still surface.
    depth = max(k * 4, 20)
    return reciprocal_rank_fusion([
        (vector_search(query, depth, year, trip, qv),
         CONFIG.retrieval.vector_weight),
        (fts_search(query, depth, year, trip), CONFIG.retrieval.fts_weight),
    ], k)


def search(query: str, k: int = CONFIG.retrieval.top_k, year: int | None = None,
           trip: str | None = None, mode: str | None = None,
           rare_slots: int | None = None) -> list[dict]:
    """Ranked retrieval, with a few slots reserved for rare literal matches.

    `rare_slots` is a floor, not a quota: slots go unused when the query has no
    rare term, and any rare chunk the ranker already found costs nothing. See
    `rare_term_hits` for the failure this exists to stop — a query whose answer
    contains the exact rare word the user typed, absent from the top 100.
    """
    mode = mode or CONFIG.retrieval.mode
    if rare_slots is None:
        rare_slots = CONFIG.retrieval.rare_slots
    rare_slots = min(rare_slots, max(k - 1, 0))     # never crowd out the ranking

    # Embedded once and shared: the ranked search and the rare-slot ranking both
    # need it, and it is a network round-trip to the model server.
    qv = embed_query(query) if (mode != "fts" or rare_slots) else None

    ranked = _ranked(query, k, year, trip, mode, qv)
    if not rare_slots:
        return ranked

    have = {h["chunk_id"] for h in ranked}
    missing = [r for r in rare_term_hits(query, rare_slots, year, trip, qv=qv)
               if r["chunk_id"] not in have]
    if not missing:
        return ranked

    # Appended, not prepended. These are guaranteed-relevant by one word, which
    # is weaker evidence than the ranker's judgement — they belong in the window,
    # not at the top of it. Displacing the ranker's tail is the price.
    for r in missing:
        r["_rare"] = True
    return ranked[:k - len(missing)] + missing


def snippet(text: str, query: str, width: int) -> str:
    """A window around the part that matched, not the opening of the chunk.

    A correct hit can look wrong when the matching sentence is 83% of the way
    into the chunk and the display shows the first 320 characters. Centres on
    the densest cluster of query words; falls back to the opening when the match
    was purely semantic and shares no vocabulary.
    """
    body = text.split(" — ", 1)[-1]
    flat = " ".join(body.split())
    words = [w for w in re.findall(r"[a-z']{3,}", query.lower()) if w not in STOP]
    if not words or len(flat) <= width:
        return flat[:width]

    # Score each position by how many distinct query words fall nearby.
    hits = sorted(m.start() for w in words
                  for m in re.finditer(rf"\b{re.escape(w[:6])}", flat, re.I))
    if not hits:
        return flat[:width]
    # Sorted, so ties resolve to the EARLIEST position in the cluster. Without
    # that the window can centre on a trailing common word ("dishes") and clip
    # the rare one the query was really about ("porchetta").
    best, best_n = hits[0], 0
    for h in hits:
        n = sum(1 for x in hits if h - width // 2 <= x <= h + width // 2)
        if n > best_n:
            best, best_n = h, n
    start = max(0, best - width // 3)
    if start:
        # Snap to a word boundary — "…rchetta was one of" reads as a typo.
        space = flat.find(" ", start)
        if 0 <= space < start + 25:
            start = space + 1
    out = flat[start:start + width]
    if start + width < len(flat):
        cut = out.rfind(" ")
        if cut > width - 25:
            out = out[:cut]
    return ("…" if start else "") + out


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
    ap.add_argument("--all", action="store_true",
                    help="exhaustive: EVERY entry containing the query as a "
                         "literal term, ignoring -k and both retrievers")
    ap.add_argument("--substring", action="store_true",
                    help="with --all, match inside words too (stem searches)")
    args = ap.parse_args()

    if args.all:
        rows = term_lookup(args.query, year=args.year, trip=args.trip,
                           substring=args.substring)
        if not rows:
            kind = "substring" if args.substring else "whole word"
            print(f"no entry contains {args.query!r} ({kind})")
            return
        entries = {r["entry_id"] for r in rows}
        occurrences = sum(r["_count"] for r in rows)
        print(f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}, "
              f"{len(rows)} chunk(s), {occurrences} occurrence(s) "
              f"— complete, not top-{args.k}\n")
        for r in rows:
            part = (f"  [{r['chunk_index'] + 1}/{r['n_chunks']}]"
                    if r["n_chunks"] > 1 else "")
            times = f"  x{r['_count']}" if r["_count"] > 1 else ""
            print(f"   {r['entry_date']}  {r['trip']}{part}{times}")
            print(f"   {r['chunk_id']}")
            if not args.no_text:
                print(f"   {snippet(r['text'], args.query, args.chars)}…")
            print()
        return

    year = args.year
    if year is None and (found := detect_year(args.query)):
        year = found
        print(f"(detected year {year} in the query — filtering. "
              f"Use --year 0 to disable.)\n")
    if year == 0:
        year = None

    if args.mode == "vector" and worth_a_literal_lookup(args.query):
        # Cheap enough to just answer the question the user probably meant.
        n = len(term_lookup(args.query, year=year, trip=args.trip))
        if n:
            print(f"(note: {n} entr{'y' if n == 1 else 'ies'} contain "
                  f"{args.query!r} literally. Vector search ranks by meaning and "
                  f"will not return them all — use --all for every one, or "
                  f"--mode fts to rank by the term.)\n")

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
            print(f"   {snippet(h['text'], args.query, args.chars)}…")
        print()


if __name__ == "__main__":
    main()
