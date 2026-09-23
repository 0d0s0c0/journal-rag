"""Score retrieval against questions whose answers are known.

    uv run python -m src.evaluate                    # score every question
    uv run python -m src.evaluate -k 10              # widen the window
    uv run python -m src.evaluate --failures         # only what missed
    uv run python -m src.evaluate --save baseline    # record for comparison
    uv run python -m src.evaluate --compare baseline # what moved since

Questions live in journal-data/eval/questions.txt, one per line:

    the place I had a flat tire and a kind man took me to a bike shop | 2011-02-12
    octopus while snorkelling                                         | 2014-07-06
    the time I went skydiving                                         | NONE

The expected answer is a date, an entry id, or NONE for things that never
happened. NONE questions matter as much as the rest: a system that returns
confident 0.75 matches for events that did not occur will, in Phase 4, write a
fluent answer about them.

This exists so that changes can be judged rather than felt. "Recall@1 went from
0.62 to 0.85" is a fact; "that seems better" is not, and retrieval changes
routinely fix one class of query while quietly breaking another.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

from src.search import search

EVAL_DIR = Path.home() / "playground/journal-data/eval"
QUESTIONS = EVAL_DIR / "questions.txt"

# Below this, a result is weak enough that returning nothing would be better.
WEAK = 0.60


@dataclass
class Result:
    question: str
    expected: str
    rank: int | None          # 1-based; None = not found in the window
    score: float | None       # similarity of the correct hit
    top_score: float | None   # similarity of whatever ranked first
    top_date: str | None


def load_questions(path: Path) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if not path.exists():
        return out
    for n, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        if "|" not in line:
            print(f"  questions.txt:{n}: no '|' separator, skipped")
            continue
        q, _, expected = line.partition("|")
        out.append((q.strip(), expected.strip()))
    return out


def matches(hit: dict, expected: str) -> bool:
    """A hit is correct if it is the expected entry, by date or by id."""
    if expected.upper() == "NONE":
        return False
    return expected in (hit["entry_date"], hit["entry_id"], hit["chunk_id"])


def evaluate(questions: list[tuple[str, str]], k: int) -> list[Result]:
    results: list[Result] = []
    for q, expected in questions:
        hits = search(q, k=k)
        rank = score = None
        for i, h in enumerate(hits, 1):
            if matches(h, expected):
                rank, score = i, similarity(h)
                break
        results.append(Result(
            question=q, expected=expected, rank=rank, score=score,
            top_score=similarity(hits[0]) if hits else None,
            top_date=hits[0]["entry_date"] if hits else None,
        ))
    return results


def similarity(hit: dict) -> float:
    """LanceDB returns L2 distance; unit-norm vectors make this cosine."""
    d = hit.get("_distance")
    return 1 - (d * d) / 2 if d is not None else float("nan")


def report(results: list[Result], k: int, failures_only: bool = False) -> dict:
    findable = [r for r in results if r.expected.upper() != "NONE"]
    absent = [r for r in results if r.expected.upper() == "NONE"]

    for r in results:
        hit = r.rank is not None
        if failures_only and (hit and r.rank <= 5):
            continue
        if r.expected.upper() == "NONE":
            risky = r.top_score is not None and r.top_score >= WEAK
            mark = "RISK" if risky else "ok  "
            print(f"  {mark}  [absent]  top={r.top_score:.3f} ({r.top_date})"
                  f"  {r.question[:60]}")
        elif hit:
            print(f"  {'ok  ' if r.rank <= 5 else 'far '}  rank {r.rank:<3} "
                  f"score={r.score:.3f}  {r.question[:60]}")
        else:
            print(f"  MISS  not in top {k}   top={r.top_score:.3f} "
                  f"({r.top_date})  {r.question[:60]}")

    if not findable:
        print("\nno answerable questions yet")
        return {}

    ranks = [r.rank for r in findable if r.rank is not None]
    summary = {
        "questions": len(findable),
        "recall@1": sum(1 for r in ranks if r == 1) / len(findable),
        "recall@3": sum(1 for r in ranks if r <= 3) / len(findable),
        "recall@5": sum(1 for r in ranks if r <= 5) / len(findable),
        f"recall@{k}": len(ranks) / len(findable),
        "mean_rank": statistics.mean(ranks) if ranks else None,
        "median_rank": statistics.median(ranks) if ranks else None,
        "misses": len(findable) - len(ranks),
    }
    if absent:
        risky = sum(1 for r in absent
                    if r.top_score is not None and r.top_score >= WEAK)
        summary["absent_questions"] = len(absent)
        summary["absent_with_confident_hit"] = risky

    print(f"\n  {'questions':<26}{summary['questions']}")
    for key in ("recall@1", "recall@3", "recall@5", f"recall@{k}"):
        print(f"  {key:<26}{summary[key]:.2f}")
    if summary["mean_rank"] is not None:
        print(f"  {'mean rank (when found)':<26}{summary['mean_rank']:.1f}")
    print(f"  {'misses':<26}{summary['misses']}")
    if absent:
        print(f"  {'absent questions':<26}{summary['absent_questions']}")
        print(f"  {'...with a >=%.2f top hit' % WEAK:<26}"
              f"{summary['absent_with_confident_hit']}   <- would mislead Phase 4")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-k", type=int, default=10, help="window to search within")
    ap.add_argument("--failures", action="store_true", help="show only misses")
    ap.add_argument("--save", metavar="NAME", help="save this run for comparison")
    ap.add_argument("--compare", metavar="NAME", help="diff against a saved run")
    args = ap.parse_args()

    questions = load_questions(QUESTIONS)
    if not questions:
        EVAL_DIR.mkdir(parents=True, exist_ok=True)
        if not QUESTIONS.exists():
            QUESTIONS.write_text(TEMPLATE)
            print(f"created {QUESTIONS}\nAdd questions, then re-run.")
        else:
            print(f"no questions in {QUESTIONS}")
        return

    print(f"{len(questions)} question(s), window k={args.k}\n")
    results = evaluate(questions, args.k)
    summary = report(results, args.k, args.failures)

    if args.save:
        EVAL_DIR.mkdir(parents=True, exist_ok=True)
        path = EVAL_DIR / f"run-{args.save}.json"
        path.write_text(json.dumps(
            {"summary": summary, "results": [asdict(r) for r in results]}, indent=2))
        print(f"\nsaved {path}")

    if args.compare:
        path = EVAL_DIR / f"run-{args.compare}.json"
        if not path.exists():
            sys.exit(f"no saved run at {path}")
        before = json.loads(path.read_text())
        print(f"\nvs {args.compare}:")
        for key in ("recall@1", "recall@3", "recall@5", "misses"):
            old, new = before["summary"].get(key), summary.get(key)
            if old is None or new is None:
                continue
            delta = new - old
            arrow = "  " if abs(delta) < 1e-9 else ("up" if delta > 0 else "DOWN")
            print(f"  {key:<12} {old:>6.2f} -> {new:>6.2f}  {arrow}")
        old_ranks = {r["question"]: r["rank"] for r in before["results"]}
        for r in results:
            o, nw = old_ranks.get(r.question), r.rank
            if o != nw:
                print(f"    {r.question[:52]:<54} {o} -> {nw}")


TEMPLATE = """\
# Retrieval evaluation questions.
#
#   <question in your own words> | <expected entry date, entry id, or NONE>
#
# Write questions about things that happened ONCE and that you would recognise,
# phrased the way you would ask rather than the way the journal is written —
# that is what tests retrieval rather than memory.
#
# Good:  the restaurant where the waiter forgot to give me my change | 2019-02-13
# Good:  the day it rained while we walked around the temple ruins   | 2013-08-24
# Poor:  what were my favourite meals                                 (needs Phase 5)
# Poor:  first year I visited Zenda                                  (a metadata query)
#
# Include two or three things that never happened, marked NONE. A system that
# returns confident matches for those will invent answers in Phase 4.

the place I had a flat tire and a kind man took me to a bike shop | 2011-02-12
"""


if __name__ == "__main__":
    main()
