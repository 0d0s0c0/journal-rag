"""Score retrieval against questions whose answers are known.

    uv run python -m src.evaluate                    # score every question
    uv run python -m src.evaluate -k 10              # widen the window
    uv run python -m src.evaluate --failures         # only what missed
    uv run python -m src.evaluate --save baseline    # record for comparison
    uv run python -m src.evaluate --compare baseline # what moved since

Questions live in journal-data/eval/questions.txt, one per line:

    the day a stranger helped me repair the gramophone | 2011-02-16
    quenelle at the harbour inn                                         | 2014-07-09
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
import re
import statistics
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

from src.config import CONFIG
from src.search import search

EVAL_DIR = CONFIG.paths.eval
QUESTIONS = CONFIG.paths.questions

# Below this, a result is weak enough that returning nothing would be better.
WEAK = 0.60


@dataclass
class Result:
    question: str
    expected: list[str]
    rank: int | None          # 1-based rank of the FIRST correct hit
    score: float | None       # its similarity
    top_score: float | None   # similarity of whatever ranked first
    top_date: str | None
    found: int = 0            # how many of the expected answers the window held
    matched: str | None = None   # which one ranked first
    wrong_above: int = 0      # known-wrong entries outranking ANY correct answer
    wrong_in_window: int = 0  # known-wrong entries anywhere in the window


def load_questions(path: Path) -> list[tuple[str, list[str], list[str], list[str]]]:
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
        parts = [p.strip() for p in line.split("|")]
        q, expected = parts[0], parts[1] if len(parts) > 1 else ""
        # Comma-separated alternatives: some things happened more than once
        # ("the time I lost my sextant"), and any of them is a correct answer.
        answers = [a.strip() for a in expected.split(",") if a.strip()]
        # An optional third field lists entries known to be WRONG — the day you
        # decided not to snorkel, the day you watched other people. Retrieval
        # cannot tell these apart from real answers, so counting how often they
        # outrank the truth is the signal that should move once something reads
        # the text rather than ranking it.
        # A field may be either the known-wrong list (NOT ...) or the required
        # content list (SAYS ...). Order between them does not matter.
        wrong: list[str] = []
        says: list[str] = []
        for field in parts[2:]:
            f = field.strip()
            if f.upper().startswith("SAYS"):
                says = [a.strip() for a in f[4:].lstrip(": ").split(",") if a.strip()]
            else:
                if f.upper().startswith("NOT"):
                    f = f[3:].lstrip(": ")
                wrong = [a.strip() for a in f.split(",") if a.strip()]
        if not answers:
            # No expected answer yet — a question still being worked out. Counting
            # it as a miss would quietly depress recall and make every later
            # comparison wrong.
            print(f"  questions.txt:{n}: no expected answer yet, not scored "
                  f"-> {q.strip()[:50]}")
            continue
        out.append((q.strip(), answers, wrong, says))
    return out


def is_absent(expected: list[str]) -> bool:
    return len(expected) == 1 and expected[0].upper() == "NONE"


def matches(hit: dict, expected: list[str]) -> str | None:
    """Return the expected answer this hit satisfies, or None.

    Several answers may be listed: some episodes happened more than once, and
    any of them counts. Scoring the first one found measures what actually
    matters — whether retrieval surfaced such an entry at all.
    """
    if is_absent(expected):
        return None
    for e in expected:
        if e in (hit["entry_date"], hit["entry_id"], hit["chunk_id"]):
            return e
    return None


def evaluate(questions: list[tuple[str, str]], k: int, mode: str | None = None,
             rare_slots: int | None = None) -> list[Result]:
    results: list[Result] = []
    for q, expected, wrong, _says in questions:
        hits = search(q, k=k, mode=mode, rare_slots=rare_slots)
        rank = score = None
        matched = None
        last_right: int | None = None
        seen: set[str] = set()
        wrong_ranks: list[int] = []
        for i, h in enumerate(hits, 1):
            if matches(h, wrong):
                wrong_ranks.append(i)
            m = matches(h, expected)
            if not m:
                continue
            seen.add(m)
            last_right = i
            if rank is None:
                rank, score, matched = i, similarity(h), m
        results.append(Result(
            question=q, expected=expected, rank=rank, score=score,
            top_score=similarity(hits[0]) if hits else None,
            top_date=hits[0]["entry_date"] if hits else None,
            found=len(seen), matched=matched,
            wrong_in_window=len(wrong_ranks),
            # Counted against the LAST correct hit, not the first. A wrong
            # answer sitting between two correct ones still displaced one, and
            # measuring against the first hides that entirely.
            wrong_above=sum(1 for r in wrong_ranks
                            if last_right is None or r < last_right),
        ))
    return results


def similarity(hit: dict) -> float:
    """LanceDB returns L2 distance; unit-norm vectors make this cosine."""
    d = hit.get("_distance")
    return 1 - (d * d) / 2 if d is not None else float("nan")


def check_generation(questions: list[tuple[str, list[str], list[str]]],
                     k: int, mode: str | None = None,
                     rare_slots: int | None = None) -> list[dict]:
    """Score the answer, not the retrieval.

    Retrieval metrics cannot see the two failures that matter most once a model
    is in the loop. An absent question retrieves a confident 0.76 hit and looks
    fine here while the answer invents an event; and a question can retrieve the
    right entry at rank 1 and still be answered from the wrong one.

    So: absent questions are scored on whether the model REFUSED, and answerable
    ones on whether the dates it cited are dates we expected.
    """
    from src.ask import ask                      # imported here — costs a model load

    rows: list[dict] = []
    for q, expected, wrong, says in questions:
        r = ask(q, k=k, mode=mode, rare_slots=rare_slots)
        cited = set(re.findall(r"\d{4}-\d{2}-\d{2}", r["answer"]))
        # An expected value may be a date, an entry id or a chunk id; all of them
        # begin with the date, so containment is the right test.
        def hits_any(date: str, pool: list[str]) -> bool:
            return any(date in e for e in pool)
        rows.append({
            "question": q,
            "absent": is_absent(expected),
            "refused": r["refused"],
            "cited": sorted(cited),
            "cited_right": sorted(d for d in cited if hits_any(d, expected)),
            "cited_wrong": sorted(d for d in cited if hits_any(d, wrong)),
            "uncited": not cited and not r["refused"],
            # Citations prove provenance, not correctness. Measured on this
            # archive: an answer that cited both expected entries while saying
            # "the journals do not specify the name of the accommodation" scored
            # as a pass, as did one that named two places and silently omitted a
            # third. Every failure found by hand was invisible here. `SAYS`
            # asserts what the answer must actually contain.
            "says": list(says),
            "says_missing": [t for t in says
                             if t.lower() not in r["answer"].lower()],
            "seconds": round(r["retrieval_s"] + r["generate_s"], 1),
        })
    return rows


def report_generation(rows: list[dict]) -> dict:
    absent = [r for r in rows if r["absent"]]
    real = [r for r in rows if not r["absent"]]

    print("\n── generation " + "─" * 55)
    for r in rows:
        if r["absent"]:
            mark = "ok  " if r["refused"] else "INVENTED"
            detail = "refused" if r["refused"] else f"answered, cited {r['cited']}"
        elif r["refused"]:
            mark = "REFUSED"
            detail = "said it could not find an answer"
            # A refusal can still name dates while explaining itself, and those
            # count toward the known-wrong tally. Hiding them here made the
            # summary report a citation no row accounted for.
            if r["cited"]:
                detail += f" (mentioned {', '.join(r['cited'])}"
                if r["cited_wrong"]:
                    detail += " — KNOWN-WRONG"
                detail += ")"
        elif r["says_missing"]:
            mark = "WRONG"
            detail = (f"cited {', '.join(r['cited_right']) or 'nothing'} but never "
                      f"said: {', '.join(r['says_missing'])}")
        elif r["cited_right"]:
            mark = "ok  "
            detail = f"cited {', '.join(r['cited_right'])}"
            if r["cited_wrong"]:
                detail += f"  (also known-wrong {', '.join(r['cited_wrong'])})"
        elif r["uncited"]:
            mark = "UNCITED"
            detail = "answered with no date — ungrounded"
        else:
            mark = "OFF  "
            detail = f"cited only {', '.join(r['cited']) or 'nothing'}"
        print(f"  {mark:<8} {r['seconds']:>5.1f}s  {r['question'][:46]:<46} {detail}")

    # Refusal on absent questions is the headline number: it is the only defence
    # against a fluent answer about something that never happened, and no
    # similarity threshold can provide it.
    stats = {
        "refused_absent": sum(r["refused"] for r in absent),
        "absent": len(absent),
        "grounded": sum(bool(r["cited_right"]) for r in real),
        "real": len(real),
        # Only in ANSWERS. A refusal that names a known-wrong entry is
        # explaining what it looked at and rejected, which is the behaviour we
        # want; counting it as a bad citation punished the model for showing
        # its work.
        "cited_known_wrong": sum(bool(r["cited_wrong"]) and not r["refused"]
                                 for r in real),
        "refused_real": sum(r["refused"] for r in real),
        "uncited_real": sum(r["uncited"] for r in real),
        "median_seconds": round(statistics.median(r["seconds"] for r in rows), 1),
        "content_checked": sum(1 for r in real if r["says"]),
        "content_complete": sum(1 for r in real if r["says"] and not r["says_missing"]),
    }
    n_abs, n_real = max(len(absent), 1), max(len(real), 1)
    print()
    print(f"  refused when absent      {stats['refused_absent']}/{len(absent)}"
          f"   ({stats['refused_absent']/n_abs:.0%})   <- the one that matters")
    print(f"  cited an expected date   {stats['grounded']}/{len(real)}"
          f"   ({stats['grounded']/n_real:.0%})")
    print(f"  cited a known-wrong date {stats['cited_known_wrong']}/{len(real)}")
    print(f"  refused a real question  {stats['refused_real']}/{len(real)}")
    print(f"  answered with no date    {stats['uncited_real']}/{len(real)}")
    if stats["content_checked"]:
        print(f"  SAID what it must        {stats['content_complete']}"
              f"/{stats['content_checked']}"
              f"   <- content, not just citation")
    print(f"  median time              {stats['median_seconds']}s")
    return stats


def report(results: list[Result], k: int, failures_only: bool = False) -> dict:
    findable = [r for r in results if not is_absent(r.expected)]
    absent = [r for r in results if is_absent(r.expected)]

    for r in results:
        hit = r.rank is not None
        if failures_only and (hit and r.rank <= 5):
            continue
        if is_absent(r.expected):
            risky = r.top_score is not None and r.top_score >= WEAK
            mark = "RISK" if risky else "ok  "
            print(f"  {mark}  [absent]  top={r.top_score:.3f} ({r.top_date})"
                  f"  {r.question[:60]}")
        elif hit:
            of = f"  [{r.found}/{len(r.expected)} found]" if len(r.expected) > 1 else ""
            bad = f"  {r.wrong_above} known-wrong above" if r.wrong_above else ""
            print(f"  {'ok  ' if r.rank <= 5 else 'far '}  rank {r.rank:<3} "
                  f"score={r.score:.3f}  {r.question[:52]}{of}{bad}")
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

    # Completeness, kept separate from recall on purpose.
    #
    # recall@1 asks "did SOMETHING correct rank first". For a question expecting
    # one entry that is the whole story; for "which entries mention X" it is
    # actively misleading. A name occurring in 4 entries returned 1 of them and
    # still scored a recall@1 hit, because the one it found ranked first — the
    # harness printed [1/4] beside it and then averaged it into nothing.
    multi = [r for r in findable if len(r.expected) > 1]
    if multi:
        got = sum(r.found for r in multi)
        want = sum(len(r.expected) for r in multi)
        summary["multi_answer_questions"] = len(multi)
        summary[f"completeness@{k}"] = got / want
        summary["answers_found"] = got
        summary["answers_expected"] = want
    if any(r.wrong_above or r.wrong_in_window for r in findable):
        summary["wrong_above_total"] = sum(r.wrong_above for r in findable)
        summary["wrong_in_window_total"] = sum(r.wrong_in_window for r in findable)
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
    if "multi_answer_questions" in summary:
        print(f"  {'multi-answer questions':<26}{summary['multi_answer_questions']}")
        print(f"  {f'completeness@{k}':<26}{summary[f'completeness@{k}']:.2f}"
              f"   ({summary['answers_found']}/{summary['answers_expected']}"
              f" expected entries surfaced)")
    if "wrong_above_total" in summary:
        print(f"  {'wrong outranking a right':<26}{summary['wrong_above_total']}"
              f"   <- should fall once something reads the text")
        print(f"  {'known-wrong in window':<26}{summary['wrong_in_window_total']}")
    if absent:
        print(f"  {'absent questions':<26}{summary['absent_questions']}")
        print(f"  {'...with a >=%.2f top hit' % WEAK:<26}"
              f"{summary['absent_with_confident_hit']}   <- would mislead Phase 4")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-k", type=int, default=10, help="window to search within")
    ap.add_argument("--failures", action="store_true", help="show only misses")
    ap.add_argument("--mode", choices=["vector", "fts", "hybrid"],
                    help="retrieval mode to score (default: config)")
    ap.add_argument("--rare-slots", type=int,
                    help="slots reserved for rare literal matches (0 disables)")
    ap.add_argument("--save", metavar="NAME", help="save this run for comparison")
    ap.add_argument("--compare", metavar="NAME", help="diff against a saved run")
    ap.add_argument("--generate", action="store_true",
                    help="also run the model and score refusals and citations "
                         "(slow — one generation per question)")
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

    print(f"{len(questions)} question(s), window k={args.k}, "
          f"mode={args.mode or CONFIG.retrieval.mode}\n")
    results = evaluate(questions, args.k, mode=args.mode,
                       rare_slots=args.rare_slots)
    summary = report(results, args.k, args.failures)

    if args.generate:
        # k here is the PRODUCTION excerpt count, not the retrieval window. -k 10
        # widens the window to see how far down a correct answer sits; feeding the
        # model 10 excerpts would measure a configuration nobody runs.
        summary["generation"] = report_generation(
            check_generation(questions, CONFIG.retrieval.top_k, mode=args.mode,
                             rare_slots=args.rare_slots))

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
        old = {r["question"]: r["rank"] for r in before["results"]}
        new_qs = [r for r in results if r.question not in old]
        gone = [q for q in old if q not in {r.question for r in results}]
        if new_qs or gone:
            # Recall is a fraction of the question set, so it is not comparable
            # across different sets. Saying so beats quietly printing a delta.
            print(f"  NOTE: question set changed "
                  f"(+{len(new_qs)} new, -{len(gone)} removed)."
                  f" Rates above are not directly comparable.")
        for r in results:
            if r.question not in old:
                print(f"    {r.question[:52]:<54} NEW -> {r.rank}")
                continue
            o = old[r.question]
            if o != r.rank:
                arrow = ("worse" if r.rank is None or (o is not None and r.rank > o)
                         else "better")
                print(f"    {r.question[:52]:<54} {o} -> {r.rank}  {arrow}")


TEMPLATE = """\
# Retrieval evaluation questions.
#
#   <question> | <expected date(s), id, or NONE> | NOT <known-wrong date(s)>
#
# Several answers separated by commas means ANY of them is correct — for
# episodes that happened more than once ("the time I lost my sextant").
#
# Write questions about things that happened ONCE and that you would recognise,
# phrased the way you would ask rather than the way the journal is written —
# that is what tests retrieval rather than memory.
#
# Good:  the inn where the landlord forgot my change | 2019-02-13
# Good:  the day it rained while we walked around the old orangery   | 2013-09-01
# Poor:  what were my favourite meals                                 (needs Phase 5)
# Poor:  first year I visited Zenda                                  (a metadata query)
#
# Include two or three things that never happened, marked NONE. A system that
# returns confident matches for those will invent answers in Phase 4.

the day a stranger helped me repair the gramophone | 2011-02-16
# the time I lost or broke my sextant | 2015-03-19, 2019-08-21, 2023-04-14
"""


if __name__ == "__main__":
    main()
