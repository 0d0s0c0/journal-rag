"""Tests for the pattern lists the pre-commit hook checks against.

Pure functions only — no archive, no index. Synthetic entries and questions
stand in for the real ones, which is the whole point: these tests must be
readable in a public repo.

Each case here corresponds to something that actually reached a public commit
before the check existed.
"""

from __future__ import annotations

from src.private_names import (IDENTIFYING_MAX_ENTRIES, collect_dates,
                               collect_phrases, collect_names, read_questions)


def entry(text: str, date: str = "2019-08-19") -> dict:
    return {"entry_date": date, "text": text}


class TestDates:
    def test_collects_every_entry_date(self):
        entries = [entry("a", "2019-08-19"), entry("b", "2020-01-04"),
                   entry("c", "2019-08-19")]
        assert collect_dates(entries) == {"2019-08-19", "2020-01-04"}

    def test_tolerates_entries_without_a_date(self):
        assert collect_dates([{"text": "x"}, entry("y", "2019-08-19")]) == {"2019-08-19"}


class TestQuestionParsing:
    def test_reads_question_and_absent_flag(self, tmp_path):
        f = tmp_path / "q.txt"
        f.write_text("# a comment\n"
                     "\n"
                     "the day the gramophone broke | 2019-08-19\n"
                     "the time I flew to the moon | NONE\n"
                     "two answers | 2019-08-19, 2020-01-04 | NOT 2021-05-05\n")
        assert read_questions(f) == [
            ("the day the gramophone broke", False),
            ("the time I flew to the moon", True),
            ("two answers", False),
        ]

    def test_missing_file_is_not_an_error(self, tmp_path):
        assert read_questions(tmp_path / "nope.txt") == []


class TestPhrases:
    def test_whole_question_becomes_a_phrase(self):
        phrases, _ = collect_phrases(
            [("the day a stranger repaired the gramophone", False)], [])
        assert "the day a stranger repaired the gramophone" in phrases

    def test_adjacent_content_words_become_a_phrase(self):
        # Neither word is rare on its own; together they name an episode. This
        # is the case single-word blocking cannot catch.
        phrases, _ = collect_phrases([("the glass orangery at the inn", False)], [])
        assert "glass orangery" in phrases

    def test_absent_questions_are_skipped(self):
        # Things that never happened are not archive content, and blocking them
        # would reject this project's own writing about refusal testing.
        phrases, words = collect_phrases([("the time I climbed Everest", True)], [])
        assert phrases == set() and words == set()

    def test_very_short_phrases_are_dropped(self):
        # A one-word question is handled by the word list, not here; a short
        # phrase would match far too much ordinary prose.
        phrases, _ = collect_phrases([("Margarethe", False)], [])
        assert not any(len(p) <= 8 for p in phrases)


class TestIdentifyingWords:
    def test_a_rare_word_is_identifying(self):
        entries = [entry("we ate a quenelle")] + [entry("nothing here") for _ in range(50)]
        _, words = collect_phrases([("the time I ate a quenelle", False)], entries)
        assert "quenelle" in words

    def test_a_common_word_is_not(self):
        # Appearing in many entries means the word describes a category, not an
        # episode. Blocking it would reject ordinary documentation.
        entries = [entry("the gramophone again") for _ in range(IDENTIFYING_MAX_ENTRIES + 5)]
        _, words = collect_phrases([("the gramophone repair", False)], entries)
        assert "gramophone" not in words

    def test_a_word_absent_from_the_archive_is_not_blocked(self):
        # The asker's own paraphrase ("accidently") is not journal content.
        _, words = collect_phrases([("the inn where i accidently paid", False)],
                                   [entry("unrelated")])
        assert "accidently" not in words

    def test_ordinary_english_is_never_identifying(self):
        # Guarded by NOT_IDENTIFYING: these are rare in a small corpus by luck,
        # not because they name anything.
        entries = [entry("the phone and the shop")]
        _, words = collect_phrases([("the shop where I lost my phone", False)], entries)
        assert not ({"phone", "shop", "lost"} & words)


class TestNames:
    def test_generic_words_are_not_blocked(self, tmp_path):
        (tmp_path / "2019").mkdir()
        (tmp_path / "2019" / "beach").mkdir()
        (tmp_path / "2019" / "vespugia").mkdir()
        names = collect_names(tmp_path)
        assert "vespugia" in names
        assert "beach" not in names        # too common to block on

    def test_multi_word_labels_split(self, tmp_path):
        (tmp_path / "mt vespugia").mkdir()
        names = collect_names(tmp_path)
        assert "mt vespugia" in names and "vespugia" in names


class TestReconstructedCoverage:
    """entries.jsonl does not know about entries rebuilt from photo captions.

    Both halves of this were real bugs. Reading only entries.jsonl left 33 real
    dates unguarded; the fix then read the wrong id field, matched nothing,
    appended every chunk in the archive and doubled every word frequency —
    which lifted six identifying words above the rarity threshold and silently
    stopped blocking them. A coverage check that quietly loses coverage is
    worse than none.
    """

    def test_finds_rows_whose_entry_is_missing(self):
        from src.private_names import rows_missing_from_entries
        entries = [{"id": "a", "text": "x"}]
        chunks = [{"entry_id": "a", "text": "x"}, {"entry_id": "b", "text": "y"}]
        assert rows_missing_from_entries(entries, chunks) == [chunks[1]]

    def test_matches_across_the_differing_field_names(self):
        # entries.jsonl says `id`; chunks.jsonl says `entry_id`.
        from src.private_names import rows_missing_from_entries
        entries = [{"id": "a"}, {"id": "b"}]
        chunks = [{"entry_id": "a"}, {"entry_id": "b"}]
        assert rows_missing_from_entries(entries, chunks) == []

    def test_dates_union_every_source(self):
        entries = [entry("x", "2019-08-19")]
        chunks = [{"entry_id": "r", "entry_date": "2043-01-25"}]
        assert collect_dates(entries, chunks) == {"2019-08-19", "2043-01-25"}
