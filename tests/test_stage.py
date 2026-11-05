"""Tests for statement staging and its mechanical verification.

The verification is the only thing standing between a confident misattribution
and a fact table, so it is tested far more heavily than the generation around
it. Every case below is a failure that actually occurred on this archive.
"""

from __future__ import annotations

from src.stage import names_in, norm, parse, verify


class TestNorm:
    def test_collapses_whitespace(self):
        assert norm("a\n  b\tc") == "a b c"

    def test_folds_word_processor_punctuation(self):
        # The source documents are Word files: curly quotes and en dashes are
        # what autocorrect produces, and a model quoting them straight would
        # otherwise fail the verbatim check for no good reason.
        assert norm("don’t “go” – now") == norm("don't \"go\" - now")

    def test_case_insensitive(self):
        assert norm("The Monastery") == norm("the monastery")


class TestNamesIn:
    def test_finds_a_capitalised_name(self):
        assert "vespugia" in names_in("We sailed to Vespugia that morning.")

    def test_ignores_the_first_word_of_a_sentence(self):
        # Sentence-initial capitalisation says nothing. Without this, every
        # statement beginning "The..." claims a name called "The".
        assert names_in("The weather was fine.") == set()

    def test_ignores_pronouns_and_articles_mid_sentence(self):
        assert names_in("Later I saw that The weather held.") == set()

    def test_ignores_weekdays_and_months(self):
        assert names_in("It rained on Tuesday and again in March.") == set()

    def test_finds_names_in_a_later_sentence(self):
        got = names_in("It was fine. Later we reached Zenda Bay.")
        assert {"zenda", "bay"} <= got


class TestVerify:
    ENTRY = ("2019-08-19, ruritania — We stopped at one other monastery, "
             "Borduria, before Vespugia. I followed the crowd up the street "
             "to the gate. The monk said lunch was ready in the refectory.")

    def test_accepts_a_supported_statement(self):
        assert verify("The monk said lunch was ready in the refectory.",
                      "The monk said lunch was ready in the refectory.",
                      self.ENTRY) is None

    def test_rejects_a_name_its_evidence_does_not_contain(self):
        """The failure this whole mechanism exists for.

        The entry names one place in passing and another as the destination;
        the model attached the meal to the one mentioned nearest. The evidence
        quote names neither, so the claim is unsupported.
        """
        why = verify("Lunch at the Borduria monastery was spinach.",
                     "The monk said lunch was ready in the refectory.",
                     self.ENTRY)
        assert why and "borduria" in why

    def test_rejects_invented_evidence(self):
        assert verify("We ate at noon.", "We ate at precisely noon.",
                      self.ENTRY) == "evidence not found verbatim in the entry"

    def test_rejects_paraphrased_evidence(self):
        # Observed: the model inserted a word into its own quote. A near-miss
        # is still a quote the entry does not contain.
        why = verify("The room was stifling.",
                     "The room on the fourth floor had been stifling.",
                     "The room on the fourth floor had 6 beds and was stifling.")
        assert why == "evidence not found verbatim in the entry"

    def test_accepts_a_name_present_in_the_evidence(self):
        assert verify("The boat stopped at Borduria.",
                      "We stopped at one other monastery, Borduria, before "
                      "Vespugia.", self.ENTRY) is None

    def test_tolerates_whitespace_and_curly_quotes_in_the_quote(self):
        entry = "He said “it’s fine” and left."
        assert verify("He said it was fine.",
                      'He said "it\'s  fine" and left.', entry) is None

    def test_requires_both_fields(self):
        assert verify("", "x", self.ENTRY) == "empty statement"
        assert verify("x", "", self.ENTRY) == "no evidence given"


class TestParse:
    def test_reads_json_lines(self):
        reply = ('{"statement": "a", "evidence": "b"}\n'
                 '{"statement": "c", "evidence": "d"}')
        assert [r["statement"] for r in parse(reply)] == ["a", "c"]

    def test_ignores_commentary_and_fences(self):
        reply = ('Here are the statements:\n'
                 '```json\n'
                 '{"statement": "a", "evidence": "b"},\n'
                 '```\n'
                 'Let me know if you need more.')
        assert [r["statement"] for r in parse(reply)] == ["a"]

    def test_skips_malformed_lines_without_losing_the_rest(self):
        reply = ('{"statement": "good", "evidence": "x"}\n'
                 '{"statement": "truncated", "evid\n'
                 '{"statement": "also good", "evidence": "y"}')
        assert [r["statement"] for r in parse(reply)] == ["good", "also good"]

    def test_requires_a_statement_field(self):
        assert parse('{"evidence": "orphan"}') == []
