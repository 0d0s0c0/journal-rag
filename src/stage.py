"""Turn entries into self-contained statements, with mechanical verification.

    uv run python -m src.stage --sample 10          # pick entries at random
    uv run python -m src.stage --dates 2019-08-19,2020-01-03
    uv run python -m src.stage --sample 10 --show   # print them (journal content)

Writes `<data_root>/statements.jsonl` and prints only counts, so the content
stays on disk unless `--show` is passed.

Why statements rather than a fixed schema: a question needing a field nobody
anticipated — a quantity, a lodging type — would otherwise require a migration
and a re-run. "The room contained six beds" is searchable without anyone having
designed a beds column.

Why whole entries: measured on this archive, the same entry yields the right
classification for a place read whole and the wrong one read as its best chunk.
Iterating over chunks is the obvious implementation and silently degrades.

THE RISK, measured before this existed: asking the model to resolve bare
references ("the monastery") to names produces confident misattributions — a
meal assigned to a place the narrator had only passed. Roughly 3 errors in 20
statements, each then detached from the context that would reveal it.

What stopped it was not a better instruction. Permitting abstention changed
nothing (the model does not feel the ambiguity, and rates its wrong answers
"high confidence"); neither did more context, nor output-format constraints.
What worked was requiring a VERBATIM quote that states each claim, and then
checking mechanically:

    evidence must appear in the entry      — no invented quotes
    names in the statement must appear     — no claim the quote does not support
    in its own evidence

A statement failing either is rejected rather than corrected. Errors become
gaps. A gap makes an answer incomplete; a fabrication makes it false, and this
project has repeatedly shown the second is far harder to notice.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import unicodedata
from pathlib import Path

from src.ask import generate
from src.config import CONFIG

PROMPT = """Below is one day's journal entry, with its date and trip label.

Rewrite it as a list of short factual statements, one per line, as JSON:
{"statement": "...", "evidence": "..."}

RULES
- Each statement must stand alone, without the rest of the entry.
- `evidence` must be copied WORD FOR WORD from the entry, and must be the part
  that states the claim. Never paraphrase it and never invent it.
- Name a place or person in a statement ONLY if your evidence quote names it.
  If the entry says "the monastery" and the name appears elsewhere, keep it as
  "the monastery" — do not substitute a name your quote does not contain.
- State what happened. Something considered and not done, or seen other people
  doing, is not something that happened — say so explicitly if you record it.
- No commentary, no numbering, nothing outside the JSON lines.

ENTRY
{entry}

STATEMENTS"""


def norm(s: str) -> str:
    """Collapse whitespace and fold the punctuation a word processor changes.

    Evidence is compared against the entry as a substring. A curly apostrophe
    where the entry has a straight one, or a line break where it has a space,
    would reject a perfectly good quote — so neither side is compared raw.
    """
    s = unicodedata.normalize("NFKC", s)
    for a, b in (("‘", "'"), ("’", "'"), ("“", '"'),
                 ("”", '"'), ("–", "-"), ("—", "-")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip().lower()


# Words that begin a sentence or are generic enough that capitalisation says
# nothing about them. Without this, "The" and "I" read as names.
NOT_A_NAME = {
    "the", "a", "an", "i", "we", "he", "she", "they", "it", "this", "that",
    "there", "then", "after", "before", "when", "while", "also", "but", "and",
    "at", "in", "on", "by", "my", "his", "her", "our", "their", "author",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
}


def names_in(text: str) -> set[str]:
    """Capitalised words that look like names, ignoring sentence openings."""
    out = set()
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        for i, tok in enumerate(re.findall(r"\b[\w']+\b", sentence)):
            if i == 0:
                continue                       # sentence-initial tells us nothing
            if tok[:1].isupper() and tok.lower() not in NOT_A_NAME:
                out.add(tok.lower())
    return out


def verify(statement: str, evidence: str, entry: str) -> str | None:
    """None if the statement is supported; otherwise why it was rejected."""
    if not statement.strip():
        return "empty statement"
    if not evidence.strip():
        return "no evidence given"
    if norm(evidence) not in norm(entry):
        return "evidence not found verbatim in the entry"
    unsupported = names_in(statement) - names_in(evidence)
    if unsupported:
        return f"names not in its evidence: {sorted(unsupported)}"
    return None


def parse(reply: str) -> list[dict]:
    """JSON objects from the model's reply, tolerating surrounding noise."""
    rows = []
    for line in reply.splitlines():
        line = line.strip().strip(",").strip("`")
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "statement" in obj:
            rows.append(obj)
    return rows


def stage_entry(entry: dict, num_ctx: int) -> tuple[list[dict], list[dict]]:
    """(kept, rejected) statements for one entry."""
    header = f"{entry['entry_date']}, {entry.get('trip') or 'unknown'}"
    body = f"{header} — {entry['text']}"
    reply, _meta = generate(PROMPT.replace("{entry}", body), num_ctx)

    kept, rejected = [], []
    for row in parse(reply):
        s, e = str(row.get("statement", "")), str(row.get("evidence", ""))
        why = verify(s, e, body)
        record = {"entry_date": entry["entry_date"], "entry_id": entry.get("id"),
                  "statement": s, "evidence": e}
        if why:
            rejected.append({**record, "rejected": why})
        else:
            kept.append(record)
    return kept, rejected


def load_entries() -> list[dict]:
    return [json.loads(l) for l in CONFIG.paths.entries.read_text().splitlines()
            if l.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample", type=int, default=0, help="random entries")
    ap.add_argument("--dates", help="comma-separated entry dates")
    ap.add_argument("--seed", type=int, default=0, help="for a repeatable sample")
    ap.add_argument("--num-ctx", type=int, default=16384)
    ap.add_argument("--show", action="store_true",
                    help="print statements — this is journal content")
    args = ap.parse_args()

    entries = load_entries()
    if args.dates:
        want = {d.strip() for d in args.dates.split(",")}
        chosen = [e for e in entries if e["entry_date"] in want]
    elif args.sample:
        random.seed(args.seed)
        chosen = random.sample(entries, min(args.sample, len(entries)))
    else:
        ap.error("pass --sample N or --dates ...")

    out = CONFIG.paths.data_root / "statements.jsonl"
    rej = CONFIG.paths.data_root / "statements-rejected.jsonl"
    kept_all, rej_all = [], []

    print(f"{len(chosen)} entries\n")
    for i, e in enumerate(chosen, 1):
        kept, rejected = stage_entry(e, args.num_ctx)
        kept_all += kept
        rej_all += rejected
        n = len(kept) + len(rejected)
        pct = f"{len(rejected) / n:.0%}" if n else "-"
        print(f"  {i:>3}/{len(chosen)}  {e['entry_date']}  "
              f"{len(e['text']):>5} chars -> {n:>3} statements, "
              f"{len(rejected):>2} rejected ({pct})")

    out.write_text("\n".join(json.dumps(r) for r in kept_all) + "\n")
    rej.write_text("\n".join(json.dumps(r) for r in rej_all) + "\n")

    total = len(kept_all) + len(rej_all)
    print(f"\n  {total} statements, {len(kept_all)} kept, {len(rej_all)} rejected"
          f" ({len(rej_all) / total:.0%})" if total else "\n  nothing generated")
    if rej_all:
        reasons: dict[str, int] = {}
        for r in rej_all:
            key = r["rejected"].split(":")[0]
            reasons[key] = reasons.get(key, 0) + 1
        print("\n  why rejected:")
        for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"     {v:>4}  {k}")
    print(f"\n  kept     -> {out}")
    print(f"  rejected -> {rej}")
    print("\n  Statements are NOT printed above. Read the files to spot-check,")
    print("  or re-run with --show.")

    if args.show:
        print()
        for r in kept_all:
            print(f"  [{r['entry_date']}] {r['statement']}")


if __name__ == "__main__":
    main()
