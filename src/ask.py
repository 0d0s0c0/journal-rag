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
from src.search import search, similarity

# Hard grounding. Every clause here is aimed at a failure we measured:
#   - "only the excerpts" because the model otherwise answers from pretraining
#   - "say so plainly" because absent questions retrieve confident-looking hits
#   - the negation and agency lines because embeddings cannot encode either
#   - dates as citations because they are the only stable handle on an entry
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


def generate(prompt: str, num_ctx: int, model: str | None = None) -> tuple[str, dict]:
    payload = {
        "model": model or CONFIG.chat_model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_ctx": num_ctx},
    }
    req = urllib.request.Request(
        f"{CONFIG.ollama_host}/api/generate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.loads(r.read())
    return d.get("response", "").strip(), d


REFUSAL = re.compile(r"can'?t find that in the journals|not in the journals|"
                     r"do(es)? not (appear|contain)|no mention", re.I)


def ask(question: str, k: int | None = None, year: int | None = None,
        trip: str | None = None, mode: str | None = None,
        num_ctx: int | None = None) -> dict:
    k = k or CONFIG.retrieval.top_k
    num_ctx = num_ctx or CONFIG.generation.num_ctx

    t0 = time.perf_counter()
    hits = search(question, k=k, year=year, trip=trip, mode=mode)
    t_ret = time.perf_counter() - t0

    if not hits:
        return {"answer": "I can't find that in the journals.", "hits": [],
                "kept": [], "refused": True, "retrieval_s": t_ret, "generate_s": 0.0}

    # Leave room for the instructions and the answer itself. 4 chars/token is a
    # rough but adequate estimate for English prose.
    budget = max(num_ctx * 4 - len(PROMPT) - len(question) - 2000, 2000)
    context, kept = build_context(hits, budget)

    t0 = time.perf_counter()
    answer, meta = generate(PROMPT.format(context=context, question=question), num_ctx)
    t_gen = time.perf_counter() - t0

    return {
        "answer": answer, "hits": hits, "kept": kept,
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
    ap.add_argument("--year", type=int)
    ap.add_argument("--trip")
    ap.add_argument("--mode", choices=["hybrid", "vector", "fts"])
    ap.add_argument("--num-ctx", type=int, default=CONFIG.generation.num_ctx)
    ap.add_argument("--show-context", action="store_true",
                    help="print the excerpts sent to the model")
    ap.add_argument("--quiet", action="store_true", help="answer only")
    args = ap.parse_args()

    r = ask(args.question, k=args.k, year=args.year, trip=args.trip,
            mode=args.mode, num_ctx=args.num_ctx)

    if args.show_context:
        print("── excerpts sent to the model " + "─" * 40)
        for h in r["kept"]:
            s = similarity(h)
            print(f"[{h['entry_date']}] {h['chunk_id']}"
                  + (f"  sim {s:.3f}" if s is not None else ""))
            print(f"  {h['text'][:200]}…\n")
        print("─" * 69 + "\n")

    print(r["answer"])

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


if __name__ == "__main__":
    main()
