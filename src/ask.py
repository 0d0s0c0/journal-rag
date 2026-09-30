"""Answer a question from the journals. Retrieval plus generation.

    uv run python -m src.ask "what were the best places I snorkelled?"
    uv run python -m src.ask "where did I go in 2019?" -k 8
    uv run python -m src.ask "the inn with the glass orangery" --show-context

Retrieval finds candidate passages; the model reads them and answers. Nothing
else changes — the model is stateless, sees only what this prompt hands it, and
has no access to the archive.

Two things this step exists to fix, neither of which retrieval can:

  refusal    Questions about things that never happened score INSIDE the range
             of real ones — measured on this archive, real answers span
             0.509–0.900 and absent ones 0.578–0.760. No threshold separates
             them, so the model has to notice that the excerpts do not contain
             the answer and say so.

  reading    "we decided not to snorkel" embeds at 0.729 against its own
             opposite; "we saw snorkelers" is indistinguishable from "we
             snorkelled". Negation, agency and outcome are trivial for a model
             that reads the sentence and invisible to one that ranks vectors.

The context window is set explicitly. Ollama loads a conservative default
(~32K observed) regardless of what the model supports, and exceeding it
truncates the prompt SILENTLY — a confidently wrong answer built from a partial
prompt, with nothing in the output to say so.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request

from src.config import CONFIG
from src.search import (detect_year, literal_coverage, search,
                        similarity, term_lookup)

# Hard grounding. Every clause here is aimed at a failure we measured:
#   - "only the excerpts" because the model otherwise answers from pretraining
#   - "say so plainly" because absent questions retrieve confident-looking hits
#   - the negation and agency lines because embeddings cannot encode either
#   - dates as citations because they are the only stable handle on an entry
#
# The place-checking rule is worded narrowly on purpose. A broader version
# ("check the excerpts confirm what the question assumes") fired on ANY named
# place and refused questions retrieval had answered at rank 1. Asking the
# concrete question — do the excerpts MENTION it? — keeps those and still
# catches a question naming a place the excerpts never establish. Measured over
# three runs each: the broad wording refused 3/3 on an answerable place
# question; the narrow wording cited it 3/3 while still refusing 2/3 on an
# unconfirmable one, against 0/3 with no rule at all.
#
# The "refusing is for nothing relevant" rule was added later, for the opposite
# failure: a question whose answer WAS in the excerpts got "I can't find that in
# the journals" 3 times out of 3, because the entry described the event without
# using the questioner's word for it. Every grounding rule here pushes toward
# caution, and nothing pushed back. Measured before adopting it, 3 runs each:
# the affected question went refuse 3/3 -> cite 3/3, a second unstable one
# steadied at 3/3, and refusal on questions about things that never happened
# stayed at 12/12 — which is the only reason it was safe to add.
PROMPT = """You are answering questions about someone's personal travel journals.

Below are excerpts retrieved from those journals. Each is labelled with its date.

RULES
- Answer using ONLY these excerpts. Do not use general knowledge about places,
  food or events to fill gaps.
- If the excerpts do not contain the answer, say so plainly: "I can't find that
  in the journals." Do not offer a guess, and do not substitute something
  similar that is present.
- Read carefully for what actually happened. "We decided not to go" is not
  going. "We saw other people doing it" is not doing it. "It was disappointing"
  is not a recommendation.
- When the question names a place, date or person, first check whether the
  excerpts actually mention it. If they do, answer normally. If they never
  mention it, do not assume they are about it — a label naming a wider region
  does not establish a smaller place inside it. Say which part you cannot
  confirm instead.
- Refusing is for when the excerpts contain nothing relevant. If an excerpt is
  clearly about what the question asks but does not settle it, give that excerpt
  and say what it does not establish. Do not refuse in that case.
- Cite the date of each excerpt you rely on, like (2019-08-19).
- If the excerpts only partly answer the question, say which part is missing.
- Be concise. Do not pad, and do not restate the question.

EXCERPTS
{context}

QUESTION
{question}

ANSWER"""


def build_context(hits: list[dict], max_chars: int) -> tuple[str, list[dict]]:
    """Format excerpts for the prompt, stopping before the budget is exceeded.

    Truncating mid-excerpt would hand the model half a passage with no marker,
    so whole excerpts are dropped instead and the caller is told how many made it.
    """
    parts, used, kept = [], 0, []
    for h in hits:
        body = h["text"]
        block = f"[{h['entry_date']}] {body}"
        if used + len(block) > max_chars and parts:
            break
        parts.append(block)
        used += len(block)
        kept.append(h)
    return "\n\n".join(parts), kept


# Per-read, not per-response. Streaming delivers a token every few hundred
# milliseconds, so a genuine stall trips this in seconds — whereas a single
# whole-response timeout cannot tell a slow answer from a hung server. A 600s
# version of that produced ten minutes of silence and then no output at all.
READ_TIMEOUT = 120


def generate(prompt: str, num_ctx: int, model: str | None = None,
             on_token=None, think: bool | None = None) -> tuple[str, dict]:
    payload = {
        "model": model or CONFIG.chat_model,
        "prompt": prompt,
        "stream": True,
        # Thinking off — see config.example.yaml. With it on, this model spent
        # 429s producing 6,599 tokens and returned an empty response.
        "think": CONFIG.generation.think if think is None else think,
        "options": {"temperature": CONFIG.generation.temperature,
                    "num_ctx": num_ctx},
    }
    req = urllib.request.Request(
        f"{CONFIG.ollama_host}/api/generate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    parts: list[str] = []
    meta: dict = {}
    with urllib.request.urlopen(req, timeout=READ_TIMEOUT) as r:
        for line in r:                      # one JSON object per line
            if not line.strip():
                continue
            d = json.loads(line)
            if tok := d.get("response"):
                parts.append(tok)
                if on_token:
                    on_token(tok)
            if d.get("done"):
                meta = d                    # the final object carries the counts

    text = "".join(parts).strip()
    # Tokens generated but nothing returned is the thinking-mode signature: the
    # model reasons, the reasoning is discarded, and the caller sees "". Left
    # unflagged it reads as a refusal, which is the opposite of what happened.
    if not text and meta.get("eval_count", 0) > 0:
        raise RuntimeError(
            f"model generated {meta['eval_count']} tokens but returned no text — "
            "thinking mode is likely on and its output is being discarded; "
            "set generation.think: false"
        )
    return text, meta


REFUSAL = re.compile(r"can'?t find that in the journals|not in the journals|"
                     r"do(es)? not (appear|contain)|no mention", re.I)


def ask(question: str, k: int | None = None, year: int | None = None,
        trip: str | None = None, mode: str | None = None,
        num_ctx: int | None = None, on_token=None, on_context=None,
        rare_slots: int | None = None) -> dict:
    k = k or CONFIG.retrieval.top_k
    num_ctx = num_ctx or CONFIG.generation.num_ctx

    # A year in the question is a FILTER, not a hint. The embedding does not
    # encode dates, so without this the excerpts come from whatever years happen
    # to rank — measured on "where did I snorkel in 2022", only 2 of 5 excerpts
    # were from 2022 at all, and the model dutifully answered from the other
    # three. Filtering doubles coverage at every k. Pass year=0 to disable.
    detected = None
    if year is None:
        detected = detect_year(question)
        year = detected
    if year == 0:
        year = None

    t0 = time.perf_counter()
    hits = search(question, k=k, year=year, trip=trip, mode=mode,
                  rare_slots=rare_slots)
    t_ret = time.perf_counter() - t0

    if not hits:
        return {"answer": "I can't find that in the journals.", "hits": [],
                "kept": [], "refused": True, "retrieval_s": t_ret,
                "generate_s": 0.0, "year_filter": year,
                "year_detected": detected}

    # Leave room for the instructions and the answer itself. 4 chars/token is a
    # rough but adequate estimate for English prose.
    budget = max(num_ctx * 4 - len(PROMPT) - len(question) - 2000, 2000)
    context, kept = build_context(hits, budget)
    if on_context:
        on_context(kept)          # show what was retrieved before the wait starts

    t0 = time.perf_counter()
    answer, meta = generate(PROMPT.format(context=context, question=question),
                            num_ctx, on_token=on_token)
    t_gen = time.perf_counter() - t0

    return {
        "answer": answer, "hits": hits, "kept": kept,
        "year_filter": year, "year_detected": detected,
        "refused": bool(REFUSAL.search(answer)),
        "retrieval_s": t_ret, "generate_s": t_gen,
        "prompt_tokens": meta.get("prompt_eval_count"),
        "answer_tokens": meta.get("eval_count"),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("question")
    ap.add_argument("-k", type=int, default=CONFIG.retrieval.top_k,
                    help="excerpts to retrieve")
    ap.add_argument("--year", type=int,
                    help="restrict to a year; 0 disables the year detected "
                         "in the question")
    ap.add_argument("--trip")
    ap.add_argument("--mode", choices=["hybrid", "vector", "fts"])
    ap.add_argument("--num-ctx", type=int, default=CONFIG.generation.num_ctx)
    ap.add_argument("--show-context", action="store_true",
                    help="print the excerpts sent to the model")
    ap.add_argument("--quiet", action="store_true", help="answer only")
    args = ap.parse_args()

    def show(kept: list[dict]) -> None:
        print("── excerpts sent to the model " + "─" * 40)
        for h in kept:
            sim = similarity(h)
            print(f"[{h['entry_date']}] {h['chunk_id']}"
                  + (f"  sim {sim:.3f}" if sim is not None else ""))
            print(f"  {h['text'][:200]}…\n")
        print("─" * 69 + "\n")

    # Streamed so a slow answer is visibly slow. Without this, generation looks
    # identical to a hang until it finishes.
    def emit(tok: str) -> None:
        print(tok, end="", flush=True)

    if args.year is None and (y := detect_year(args.question)):
        print(f"(filtering to {y} — the year is in the question. "
              f"Use --year 0 to disable.)\n")

    r = ask(args.question, k=args.k, year=args.year, trip=args.trip,
            mode=args.mode, num_ctx=args.num_ctx, on_token=emit,
            on_context=show if args.show_context else None)
    print()

    if not args.quiet:
        cited = sorted(set(re.findall(r"\(?(\d{4}-\d{2}-\d{2})\)?", r["answer"])))
        print()
        print(f"  {len(r['kept'])} of {len(r['hits'])} excerpts used"
              f"  |  {r['prompt_tokens']} prompt tokens"
              f"  |  retrieval {r['retrieval_s']:.1f}s, generation {r['generate_s']:.1f}s")
        if cited:
            print(f"  cited: {', '.join(cited)}")
        elif not r["refused"]:
            print("  WARNING: no dates cited — the answer may not be grounded")
        if r["refused"]:
            print("  (refused — the excerpts did not contain the answer)")
        elif cited:
            _report_coverage(args, r, cited)


def _report_coverage(args, r: dict, cited: list[str]) -> None:
    """Say so when the answer is a top-k sample rather than the whole story.

    An incomplete answer and a complete one look identical in the output. Asking
    where I snorkelled in a given year matched 16 entries; at the default k=5 the
    model wrote a confident list drawn from 4 of them and said nothing about the
    other 12. Nothing was wrong — it answered exactly the question retrieval put
    to it — but a reader has no way to tell that from the text.

    Raising k helps and then stops helping. Measured over 3 runs each on that
    question: k=5 cited 4 entries, k=10 cited 8, k=25 cited 13, and k=50 cited
    **12** — fewer, from a retrieval set that contained all 16. More excerpts
    dilute the summary. Complete coverage is not a retrieval-depth problem, which
    is the argument for the Phase 5 experience index.
    """
    cov = literal_coverage(args.question, year=r.get("year_filter"), trip=args.trip)
    if not cov:
        return
    term, n = cov
    if n <= len(r["kept"]):
        return
    rows = term_lookup(term, year=r.get("year_filter"), trip=args.trip,
                       substring=True)
    hit = len({x["entry_date"] for x in rows} & set(cited))
    where = f" in {r['year_filter']}" if r.get("year_filter") else ""
    print(f"  COVERAGE: {n} entries mention '{term}'{where}; this answer cites "
          f"{hit}. It is a top-{args.k} sample, not a complete list.")
    print(f"            -k 25 covers more; --all lists every one:")
    print(f"            uv run python -m src.search \"{term}\" --all"
          + (f" --year {r['year_filter']}" if r.get("year_filter") else ""))


if __name__ == "__main__":
    main()
