"""Save the project conversation to a readable text file.

    uv run python -m src.transcript

Writes `<data_root>/conversation.txt` — your prompts and the assistant's
replies, in order, from the first message of the project to the latest.

REGENERATES rather than appends. Appending needs to know where it left off, and
every way of recording that is a thing that can go stale: a byte offset breaks
when a turn is edited, a turn count breaks when a message is filtered
differently, a timestamp breaks across clock changes. Rebuilding from the log is
idempotent and always correct, and the log is the authority either way.

Reads every session log for this project, not one file. A new session writes a
new log, so anything pinned to a single path works until it silently doesn't.

What is NOT included: tool calls, command output, and reasoning blocks. So a
measurement quoted in a reply is here; the raw output it came from is not. That
is roughly 1,000 tool calls and the large majority of the log.

The output contains whatever was discussed — for this project that means journal
content, so it is written outside the repository and `conversation*.txt` is
gitignored as a backstop.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from src.config import CONFIG

# Claude Code namespaces session logs by working directory, with the path
# flattened into the directory name.
LOG_ROOT = Path.home() / ".claude" / "projects"

# Messages that arrive as user turns but are not the user speaking: injected
# reminders, task notifications, the summary written at a context compaction,
# and the harness's own replies to permission prompts. Left in, they read as
# things the user said.
NOT_USER = (
    "<system-reminder>",
    "This session is being continued",
    "[Request interrupted",
    "Caveat:",
    "<command-name>",
    "<local-command",
    "[SYSTEM NOTIFICATION",
    "<task-notification>",
    "The user doesn't want to proceed",
    "Your questions have been answered",
)

SHELL_RE = re.compile(r"<bash-input>(.*?)</bash-input>", re.S)


def project_logs(cwd: Path | None = None) -> list[Path]:
    """Session logs for this project, oldest first."""
    cwd = cwd or Path.cwd()
    slug = str(cwd).replace("/", "-")
    d = LOG_ROOT / slug
    if not d.is_dir():
        return []
    return sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)


def message_text(msg: dict) -> str:
    """Plain text of a message, ignoring tool and reasoning blocks."""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def is_real_user_turn(text: str) -> bool:
    head = text.lstrip()
    return not any(head.startswith(m) or m in text[:200] for m in NOT_USER)


def extract(logs: list[Path]) -> tuple[list[dict], int]:
    """(turns, number of injected pseudo-user messages skipped)."""
    turns: list[dict] = []
    skipped = 0
    for log in logs:
        for line in log.read_text(errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue          # a partially written final line
            kind = event.get("type")
            msg = event.get("message") or {}
            if kind == "user":
                text = message_text(msg).strip()
                if not text:
                    continue
                if not is_real_user_turn(text):
                    skipped += 1
                    continue
                shell = SHELL_RE.match(text)
                turns.append({
                    "prompt": shell.group(1).strip() if shell else text,
                    "shell": bool(shell),
                    "reply": [],
                })
            elif kind == "assistant" and turns:
                for b in msg.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                        turns[-1]["reply"].append(b["text"])
    return turns, skipped


def render(turns: list[dict], n_logs: int) -> str:
    bar, rule = "=" * 78, "-" * 78
    out = [
        "Journal RAG — conversation transcript",
        bar,
        f"Regenerated {datetime.now():%Y-%m-%d %H:%M} from {n_logs} session log(s).",
        f"{len(turns)} exchanges, from the first message of the project to the latest.",
        "",
        "Prompts and replies only. Tool calls, command output and reasoning",
        "blocks are omitted, so a measurement quoted in a reply is here but the",
        "raw output it came from is not.",
        "",
        "CONTAINS JOURNAL CONTENT. Lives outside the repository on purpose and",
        "must never be committed.",
        "",
        "Rebuild with:  uv run python -m src.transcript",
    ]
    for i, t in enumerate(turns, 1):
        label = "YOU (ran in the shell)" if t["shell"] else "YOU"
        out += ["", "", bar, f"  [{i}]  {label}", bar, "", t["prompt"].rstrip()]
        if t["reply"]:
            out += ["", rule, "  CLAUDE", rule, "",
                    "\n\n".join(r.rstrip() for r in t["reply"])]
        elif not t["shell"]:
            out += ["", "  [no reply recorded — this turn was interrupted before "
                    "any text was written]"]
    return "\n".join(out) + "\n"


def main() -> None:
    logs = project_logs()
    if not logs:
        print(f"no session logs under {LOG_ROOT}")
        print("  (the directory name is the working directory with / replaced by -)")
        return

    turns, skipped = extract(logs)
    out = CONFIG.paths.data_root / "conversation.txt"
    out.write_text(render(turns, len(logs)))

    print(f"wrote {out}")
    print(f"  {len(turns):>5} exchanges")
    print(f"  {sum(1 for t in turns if t['shell']):>5} shell commands you ran")
    print(f"  {sum(1 for t in turns if not t['reply'] and not t['shell']):>5} "
          f"interrupted turns with no reply")
    print(f"  {skipped:>5} injected messages skipped (not things you said)")
    print(f"  {out.stat().st_size / 1024:>5.0f} KB from {len(logs)} log file(s)")


if __name__ == "__main__":
    main()
