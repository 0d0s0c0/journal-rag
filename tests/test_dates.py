"""Date-line recognition is load-bearing: it is the only structure in the archive.

A false negative silently merges two entries under the wrong date. A false
positive splits one entry in two. Neither raises an error, so these cases are
checked here rather than discovered later in retrieval results.
"""

import pytest

from src.dates import DATE_RE, NEAR_RE, parse_date_line

# Variants observed or plausible in the real journals.
VALID = [
    ("Apr. 21", (4, 21)),      # the common form — period after the month
    ("Apr 21", (4, 21)),
    ("April 21", (4, 21)),
    ("Apr 21.", (4, 21)),
    ("Apr 21st", (4, 21)),
    ("Sept. 3", (9, 3)),
    ("Sep. 3", (9, 3)),
    ("Sep 3", (9, 3)),
    ("Sep.3", (9, 3)),        # no space — would otherwise vanish silently
    ("September 3", (9, 3)),
    ("May 1", (5, 1)),
    ("Dec. 28", (12, 28)),
    ("  Jun  4  ", (6, 4)),
    ("March 7th", (3, 7)),
    ("JAN 9", (1, 9)),
]

# Prose that begins with a month prefix. A naive "May[a-z]*" pattern matches
# several of these, which would split entries mid-sentence.
PROSE = [
    "Mayonnaise was involved",
    "April showers all day",
    "Marched up the hill",
    "Augmented reality museum",
    "Decided to stay in",
    "We left on the 21st",
    "January was a blur but we made it",
]


@pytest.mark.parametrize("line,expected", VALID)
def test_valid_date_lines(line, expected):
    assert parse_date_line(line) == expected


@pytest.mark.parametrize("line", PROSE)
def test_prose_is_not_a_date(line):
    assert parse_date_line(line) is None


@pytest.mark.parametrize("line", ["Jun 15   Ha Long Bay", "Apr. 21 Klow"])
def test_same_line_title_is_a_near_miss_not_a_date(line):
    """Ambiguous, so surfaced by the probe rather than silently accepted."""
    assert DATE_RE.match(line) is None
    assert NEAR_RE.match(line) is not None


@pytest.mark.parametrize("line", ["Feb 30", "Jun 0", "Apr 99"])
def test_impossible_days(line):
    """Day-of-month sanity. Feb 30 parses structurally; validity is calendar
    work left to the caller, but 0 and >31 are rejected outright."""
    result = parse_date_line(line)
    if result is not None:
        assert 1 <= result[1] <= 31
