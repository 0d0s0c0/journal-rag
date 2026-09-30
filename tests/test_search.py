"""Tests for retrieval helpers.

The pure logic is tested unconditionally. The two tests that need a built index
skip rather than fail when there is none, so a fresh clone still runs green — the
index is derived from private data that is not in this repo.
"""

from __future__ import annotations

import pytest

from src.search import COLUMNS, detect_year, worth_a_literal_lookup


class TestWorthALiteralLookup:
    @pytest.mark.parametrize("query", [
        "Margarethe",
        "Vespugia",
        "Mt Vespugia",           # two content words
        "zarzuela",
        "the Margarethe",        # stopword does not count
    ])
    def test_short_queries_are_worth_trying(self, query):
        assert worth_a_literal_lookup(query)

    @pytest.mark.parametrize("query", [
        "the inn where i accidently left a telegram behind",
        "the day a stranger helped me repair the gramophone",
        "inn with the glass orangery",
        "the times a stranger helped with repairs",
    ])
    def test_long_descriptions_are_not(self, query):
        assert not worth_a_literal_lookup(query)

    def test_gate_is_deliberately_loose(self):
        # "which places did I snorkel at" reduces to two content words and so
        # passes the gate. That is acceptable: the literal scan then matches
        # nothing and the caller stays silent. The gate exists to avoid scanning
        # on every query, not to classify intent.
        assert worth_a_literal_lookup("which places did I snorkel at")

    @pytest.mark.parametrize("query", ["", "   ", "the and of", "a", "it is"])
    def test_nothing_to_look_up(self, query):
        # No content terms at all is not a term lookup — there is no term.
        assert not worth_a_literal_lookup(query)

    def test_short_tokens_are_ignored(self):
        # 1-2 character tokens carry no lexical signal worth routing on.
        assert not worth_a_literal_lookup("a b c d e f g")


class TestColumns:
    def test_excludes_the_vector(self):
        # Selecting the vector on a full scan costs ~16 MB of embeddings that no
        # caller of term_lookup looks at.
        assert "vector" not in COLUMNS

    def test_carries_what_callers_print(self):
        for needed in ("chunk_id", "entry_id", "entry_date", "trip", "text",
                       "chunk_index", "n_chunks", "year"):
            assert needed in COLUMNS


def _index_or_skip():
    import lancedb
    from src.config import CONFIG
    from src.embed import TABLE, table_names
    if not CONFIG.paths.index.exists():
        pytest.skip("no index built")
    db = lancedb.connect(str(CONFIG.paths.index))
    if TABLE not in table_names(db):
        pytest.skip("no index built")
    return db


class TestTermLookupAgainstTheIndex:
    def test_whole_word_excludes_substring_matches(self):
        _index_or_skip()
        from src.search import term_lookup
        # A stem shared with a longer word: whole-word must find fewer than or
        # the same as substring, never more.
        for stem in ("mon", "san", "old"):
            whole = term_lookup(stem)
            sub = term_lookup(stem, substring=True)
            assert len(whole) <= len(sub), stem

    def test_every_returned_chunk_actually_contains_the_term(self):
        _index_or_skip()
        import re
        from src.search import term_lookup
        rows = term_lookup("the")          # common enough to return plenty
        assert rows, "expected a common word to match something"
        pat = re.compile(r"\bthe\b", re.IGNORECASE)
        assert all(pat.search(r["text"]) for r in rows)
        assert all(r["_count"] >= 1 for r in rows)

    def test_year_filter_narrows_and_never_widens(self):
        _index_or_skip()
        from src.search import term_lookup
        rows = term_lookup("the")
        years = {r["year"] for r in rows if r["year"]}
        if not years:
            pytest.skip("no years in index")
        y = sorted(years)[0]
        narrowed = term_lookup("the", year=y)
        assert 0 < len(narrowed) <= len(rows)
        assert {r["year"] for r in narrowed} == {y}

    def test_empty_term_returns_nothing(self):
        _index_or_skip()
        from src.search import term_lookup
        assert term_lookup("") == []
        assert term_lookup("   ") == []

    def test_like_wildcards_are_escaped_not_honoured(self):
        _index_or_skip()
        from src.search import term_lookup
        # Unescaped, '%' would match everything; it must be a literal instead.
        assert term_lookup("%") == []
        assert term_lookup("%%%") == []


class TestDetectYear:
    @pytest.mark.parametrize("query,expected", [
        ("where did I snorkel in 2022", 2022),
        ("what did I eat in 2019?", 2019),
        ("1994 trip", 1994),
        ("the 2043 plan", 2043),
    ])
    def test_finds_a_year(self, query, expected):
        assert detect_year(query) == expected

    @pytest.mark.parametrize("query", [
        "the time I ate a quenelle",
        "where did I snorkel",
        "room 1200",              # not a plausible journal year
        "1899 was long ago",
        "it cost 2050 altogether",   # outside the accepted year range
    ])
    def test_no_year_to_find(self, query):
        assert detect_year(query) is None

    def test_takes_the_first_when_several(self):
        # Deterministic rather than clever: a range like "2019 to 2021" is not
        # something a single equality filter can express, so it filters on the
        # first and the CLI prints which one it chose.
        assert detect_year("between 2019 and 2021") == 2019


class TestRareTermRanking:
    """How the reserved slots choose between literal matches.

    Three regressions live here, each found by measurement rather than review:

      query order   slots were filled by whichever rare term appeared first in
                    the question, so one term took all of them and a slot went
                    to an unrelated entry. Flipped an answerable question from
                    cited 4/4 to refused 4/4.
      mention count   a useless tiebreak for a noun. All nine entries containing
                    "certificate" mentioned it exactly once, so the slots went
                    to the first two by date and the right entry got nothing.
      same entry    ranking by similarity alone spent both slots on two chunks
                    of a single day, surfacing one entry instead of two.
    """

    @staticmethod
    def _stub(monkeypatch, corpus, sims=None):
        def fake_lookup(term, year=None, trip=None, substring=False):
            return [dict(r) for r in corpus.get(term, [])]
        monkeypatch.setattr("src.search.term_lookup", fake_lookup)
        # One fake dimension per chunk: cosine against [1.0] returns the score.
        monkeypatch.setattr("src.search._vectors_for",
                            lambda ids: {c: [(sims or {}).get(c, 0.0)] for c in ids})

    @staticmethod
    def _chunk(cid, count=1, entry=None):
        return {"chunk_id": cid, "entry_id": entry or cid.split("#")[0],
                "entry_date": "2019-08-19", "text": "x", "_count": count}

    QV = [1.0]

    def test_similarity_decides_not_mention_count(self, monkeypatch):
        from src.search import rare_term_hits
        self._stub(monkeypatch,
                   {"alpha": [self._chunk("near#0", count=1),
                              self._chunk("far#0", count=9)]},
                   sims={"near#0": 0.9, "far#0": 0.1})
        got = rare_term_hits("alpha", limit=1, qv=self.QV)
        # "far" says the word nine times; "near" is what the question is about.
        assert [r["chunk_id"] for r in got] == ["near#0"]

    def test_term_coverage_outranks_similarity(self, monkeypatch):
        from src.search import rare_term_hits
        self._stub(monkeypatch,
                   {"alpha": [self._chunk("both#0"), self._chunk("solo#0")],
                    "beta":  [self._chunk("both#0")]},
                   sims={"both#0": 0.3, "solo#0": 0.95})
        got = rare_term_hits("alpha beta", limit=1, qv=self.QV)
        assert [r["chunk_id"] for r in got] == ["both#0"]
        assert got[0]["_rare_terms"] == ["alpha", "beta"]

    def test_one_chunk_per_entry(self, monkeypatch):
        from src.search import rare_term_hits
        self._stub(monkeypatch,
                   {"alpha": [self._chunk("day#0", entry="day"),
                              self._chunk("day#1", entry="day"),
                              self._chunk("other#0", entry="other")]},
                   sims={"day#0": 0.9, "day#1": 0.8, "other#0": 0.5})
        got = rare_term_hits("alpha", limit=2, qv=self.QV)
        assert [r["entry_id"] for r in got] == ["day", "other"]

    def test_common_terms_get_no_slots(self, monkeypatch):
        from src.search import rare_term_hits, RARE_ENTRY_MAX
        self._stub(monkeypatch, {
            "common": [self._chunk(f"c{i}#0", entry=f"e{i}")
                       for i in range(RARE_ENTRY_MAX + 1)]})
        assert rare_term_hits("common", limit=2, qv=self.QV) == []

    def test_no_rare_terms_costs_nothing(self, monkeypatch):
        from src.search import rare_term_hits
        self._stub(monkeypatch, {})
        assert rare_term_hits("the and of it", limit=2, qv=self.QV) == []

    def test_limit_is_respected(self, monkeypatch):
        from src.search import rare_term_hits
        self._stub(monkeypatch,
                   {"alpha": [self._chunk(f"a{i}#0", entry=f"e{i}")
                              for i in range(5)]})
        assert len(rare_term_hits("alpha", limit=2, qv=self.QV)) == 2
