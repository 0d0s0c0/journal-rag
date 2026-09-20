"""Date-line recognition — the single source of truth for entry boundaries.

A date line is the *only* structure in this archive: one starts a new entry and
everything else is body text. That makes these patterns load-bearing, and it
makes a missed match expensive — nothing errors, two entries simply merge under
the wrong date.

Deliberately shared between the probe and the parser. If they carried separate
copies and drifted, the archive would be parsed differently from how it was
inspected, and the discrepancy would be invisible.

Observed variants in the real journals:

    Apr 21      Apr. 21     April 21
    Apr 21.     Apr 21st    Sept. 3
"""

from __future__ import annotations

import re

# Enumerated rather than "Jan[a-z]*" so ordinary prose cannot masquerade as a
# date: "Mayonnaise", "Marched", "Augmented" and "Decided" all start with a
# month prefix and must not match.
MONTHS = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
)

MONTH_NUM = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Month, optional period, day, optional ordinal, optional period — and NOTHING
# else on the line. The trailing anchor is deliberate: "Jun 15  Zenda"
# is left to NEAR_RE, because a same-line title is ambiguous and worth surfacing
# rather than silently accepting.
# The separator is \s* rather than \s+ so "Sep.3" parses too. Safe because the
# line must contain nothing else: a whole line reading "Sep.3" is a date, never
# prose. Without this it would fail BOTH patterns and vanish silently.
#
# The optional trailing year handles "Apr. 21, 2010", observed in a handful of
# real entries. When present it wins over the year parsed from the filename,
# which matters for any file whose name carries no year.
#
# Trailing punctuation is tolerated because at least one entry ends the date
# with a dash rather than a period.
DATE_RE = re.compile(
    rf"^\s*({MONTHS})\.?\s*(\d{{1,2}})(?:st|nd|rd|th)?\.?"
    rf"(?:\s*,?\s*(\d{{4}}))?"
    rf"\s*[.,\-–—:;]*\s*$",
    re.IGNORECASE,
)

# Begins like a date but carries more on the line.
NEAR_RE = re.compile(rf"^\s*({MONTHS})\.?\s*(\d{{1,2}})\b", re.IGNORECASE)

# Begins with a month word with no day following at all.
MONTHWORD_RE = re.compile(rf"^\s*({MONTHS})\b", re.IGNORECASE)


def parse_date_line(line: str) -> tuple[int, int, int | None] | None:
    """Return (month, day, year_or_None) for a date line, else None.

    The year is present only when written inline ("Apr. 21, 2010"). When it is,
    it takes precedence over the year parsed from the filename — some files
    (journal.docx) have no filename year at all.
    """
    m = DATE_RE.match(line or "")
    if not m:
        return None
    month = MONTH_NUM[m.group(1)[:3].lower()]
    day = int(m.group(2))
    if not 1 <= day <= 31:
        return None
    year = int(m.group(3)) if m.group(3) else None
    if year is not None and not 1900 <= year <= 2100:
        return None
    return month, day, year
