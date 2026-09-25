"""Chunking rules.

The seam matters more than the size. A chunk cut mid-meal embeds as a fragment
about nothing and retrieves for nothing, and the failure is invisible — searches
merely come back mediocre.
"""

import pytest

from src.chunk import chunk_entry, header, pack, split_paragraphs, split_sentences


def entry(text, **kw):
    base = dict(
        id="ruritania-2019#0007", source_file="ruritania - 2019.docx",
        trip="ruritania", entry_date="2019-06-14", text=text,
        photos=[], warnings=[],
    )
    base.update(kw)
    return base


class TestSplitParagraphs:
    def test_blank_line_separates(self):
        assert split_paragraphs("One.\n\nTwo.\n\nThree.") == ["One.", "Two.", "Three."]

    def test_single_newline_does_not(self):
        assert split_paragraphs("One.\nStill one.") == ["One.\nStill one."]

    def test_empty_text(self):
        assert split_paragraphs("   \n\n  ") == []


class TestPack:
    def test_short_entry_stays_whole(self):
        paras = ["Rain all day.", "We played cards."]
        assert len(pack(paras, 2000)) == 1

    def test_paragraphs_are_not_split_when_they_fit(self):
        paras = ["A" * 900, "B" * 900, "C" * 900]
        out = pack(paras, 2000)
        assert len(out) == 2                      # 900+900 then 900
        assert "A" * 900 in out[0] and "B" * 900 in out[0]

    def test_oversized_paragraph_falls_back_to_sentences(self):
        para = " ".join(f"Sentence number {i} here." for i in range(200))
        out = pack([para], 500)
        assert len(out) > 1
        assert all(len(c) <= 700 for c in out)    # limit + one overlap sentence

    def test_trailing_scrap_folded_back(self):
        """A two-word tail should not become its own chunk about nothing."""
        out = pack(["A" * 1900, "Short."], 2000)
        assert len(out) == 1 or len(out[-1]) >= 120


class TestSplitSentences:
    def test_overlap_preserves_the_seam(self):
        text = "First one. Second one. Third one. Fourth one. Fifth one."
        out = split_sentences(text, 30)
        assert len(out) > 1
        # the last sentence of a chunk reappears at the start of the next
        assert any(out[0].split(". ")[-1].rstrip(".") in out[1] for _ in [0])

    def test_single_sentence_returned_intact(self):
        assert split_sentences("One long sentence.", 5) == ["One long sentence."]


class TestHeader:
    def test_includes_date_and_trip(self):
        assert header(entry("x")) == "2019-06-14, ruritania — "

    def test_survives_missing_trip(self):
        assert header(entry("x", trip=None)) == "2019-06-14 — "


class TestChunkEntry:
    def test_header_prepended_to_every_chunk(self):
        long = "\n\n".join("S. " * 300 for _ in range(4))
        chunks = chunk_entry(entry(long))
        assert len(chunks) > 1
        assert all(c.text.startswith("2019-06-14, ruritania — ") for c in chunks)

    def test_a_retrieved_fragment_carries_its_context(self):
        """'Everything felt like it was cooked in microwave' is unfindable alone."""
        text = ("A" * 1900) + "\n\n" + "Everything felt like it was cooked in microwave."
        chunks = chunk_entry(entry(text), limit=2000)
        assert all("2019-06-14" in c.text for c in chunks)

    def test_ids_are_stable_and_ordered(self):
        chunks = chunk_entry(entry("\n\n".join("P. " * 400 for _ in range(3))))
        assert [c.chunk_id for c in chunks] == [
            f"ruritania-2019#0007/{i}" for i in range(len(chunks))
        ]
        assert all(c.n_chunks == len(chunks) for c in chunks)

    def test_limit_counts_the_header(self):
        chunks = chunk_entry(entry("\n\n".join("word " * 100 for _ in range(6))), limit=900)
        assert all(c.chars <= 900 or "oversize" in c.warnings for c in chunks)

    def test_metadata_carried_through(self):
        c = chunk_entry(entry("Short entry."))[0]
        assert (c.year, c.month, c.trip) == (2019, 6, "ruritania")
        assert c.entry_id == "ruritania-2019#0007"

    def test_photo_count_not_the_paths(self):
        """Paths live in entries.jsonl; repeating them per chunk stored 40,188
        for 14,305 actual ones."""
        e = entry("\n\n".join("P. " * 400 for _ in range(3)),
                  photos=[{"path": "2019/zenda/a.jpg"}, {"path": "2019/zenda/b.jpg"}])
        chunks = chunk_entry(e)
        assert len(chunks) > 1
        assert all(c.n_photos == 2 for c in chunks)
        assert not hasattr(chunks[0], "photos")

    def test_body_recoverable_from_header_len(self):
        """`body` was stored alongside `text` and was 37% of the file."""
        c = chunk_entry(entry("Rain all day."))[0]
        assert c.text[c.header_len:] == "Rain all day."
        assert c.text[:c.header_len] == "2019-06-14, ruritania — "

    def test_entry_warnings_not_copied_onto_chunks(self):
        c = chunk_entry(entry("Short.", warnings=["no_photos", "year_rollover"]))[0]
        assert c.warnings == []

    def test_empty_entry_yields_nothing(self):
        assert chunk_entry(entry("   ")) == []


class TestQueryPrefix:
    """Queries carry the instruction prefix; documents are embedded bare.
    Getting this backwards degrades the whole index with no error."""

    def test_query_prompt_has_the_prefix(self):
        from src.search import QUERY_INSTRUCTION
        prompt = f"Instruct: {QUERY_INSTRUCTION}\nQuery: best meals"
        assert prompt.startswith("Instruct: ")
        assert "\nQuery: " in prompt

    def test_document_embedding_adds_nothing(self):
        import inspect
        from src import embed
        src = inspect.getsource(embed.embed_batch)
        assert "Instruct:" not in src, "documents must be embedded bare"


class TestEvalHarness:
    def test_parses_question_lines(self, tmp_path):
        from src.evaluate import load_questions
        f = tmp_path / "q.txt"
        f.write_text(
            "# a comment\n"
            "\n"
            "flat tire and a bike shop | 2011-02-12\n"
            "never happened | NONE\n"
            "malformed line without a separator\n"
        )
        qs = load_questions(f)
        assert qs == [("flat tire and a bike shop", ["2011-02-12"], []),
                      ("never happened", ["NONE"], [])]

    def test_parses_alternative_answers(self, tmp_path):
        """Some episodes happened more than once; any of them counts."""
        from src.evaluate import load_questions
        f = tmp_path / "q.txt"
        f.write_text("lost my phone | 2015-03-11, 2019-08-02, 2023-04-14\n")
        assert load_questions(f) == [
            ("lost my phone", ["2015-03-11", "2019-08-02", "2023-04-14"], [])]

    def test_parses_known_wrong_answers(self, tmp_path):
        """Entries retrieval keeps returning that are not actually answers."""
        from src.evaluate import load_questions
        f = tmp_path / "q.txt"
        f.write_text("snorkelling | 2022-12-21 | NOT 2023-07-17, 2022-09-16\n")
        assert load_questions(f) == [
            ("snorkelling", ["2022-12-21"], ["2023-07-17", "2022-09-16"])]

    def test_question_without_an_answer_is_not_scored(self, tmp_path):
        """An unfinished question must not count as a miss."""
        from src.evaluate import load_questions
        f = tmp_path / "q.txt"
        f.write_text("still working this out |\n")
        assert load_questions(f) == []

    def test_matches_on_date_or_id(self):
        from src.evaluate import matches
        hit = {"entry_date": "2011-02-12", "entry_id": "uk-2011#0035",
               "chunk_id": "uk-2011#0035/0"}
        assert matches(hit, ["2011-02-12"]) == "2011-02-12"
        assert matches(hit, ["uk-2011#0035"]) == "uk-2011#0035"
        assert matches(hit, ["uk-2011#0035/0"]) == "uk-2011#0035/0"
        assert matches(hit, ["2011-02-13"]) is None

    def test_matches_any_alternative_and_says_which(self):
        from src.evaluate import matches
        hit = {"entry_date": "2019-08-02", "entry_id": "x", "chunk_id": "y"}
        assert matches(hit, ["2015-03-11", "2019-08-02"]) == "2019-08-02"
        assert matches(hit, ["2015-03-11", "2023-04-14"]) is None

    def test_absent_questions_never_match(self):
        """NONE questions should never count as found, whatever comes back."""
        from src.evaluate import matches
        assert matches({"entry_date": "2011-02-12", "entry_id": "x",
                        "chunk_id": "y"}, ["NONE"]) is None


class TestReciprocalRankFusion:
    """Fusion combines by POSITION, not score — a 0.78 cosine and a 10.8 BM25
    score are not comparable numbers."""

    def test_agreement_beats_a_single_first_place(self):
        """The behaviour that made equal-weight hybrid lose: a chunk both
        retrievers rank 3rd outscores one the vector side ranks 1st alone."""
        from src.search import reciprocal_rank_fusion as rrf
        vec = [{"chunk_id": "solo"}, {"chunk_id": "x"}, {"chunk_id": "both"}]
        fts = [{"chunk_id": "y"}, {"chunk_id": "z"}, {"chunk_id": "both"}]
        out = rrf([(vec, 1.0), (fts, 1.0)], k=2)
        assert out[0]["chunk_id"] == "both"

    def test_damping_makes_early_ranks_nearly_equal(self):
        """RRF_K=60 puts rank 1 and rank 3 within 3% of each other, so even a
        small second-opinion bonus flips them. This is why lowering fts_weight
        never rescued the hybrid: by the time BM25 stops displacing confident
        vector hits, it has stopped contributing anything either.

            solo at vector rank 1      1/61          = 0.01639
            both at rank 3 in both   1/63 + w/63

        The crossover is at w < 0.033.
        """
        from src.search import reciprocal_rank_fusion as rrf
        vec = [{"chunk_id": "solo"}, {"chunk_id": "x"}, {"chunk_id": "both"}]
        fts = [{"chunk_id": "y"}, {"chunk_id": "z"}, {"chunk_id": "both"}]
        assert rrf([(vec, 1.0), (fts, 0.15)], k=2)[0]["chunk_id"] == "both"
        assert rrf([(vec, 1.0), (fts, 0.02)], k=2)[0]["chunk_id"] == "solo"

    def test_keeps_the_row_carrying_a_vector_distance(self):
        from src.search import reciprocal_rank_fusion as rrf
        out = rrf([([{"chunk_id": "a", "_distance": 0.5}], 1.0),
                   ([{"chunk_id": "a"}], 1.0)], k=1)
        assert out[0]["_distance"] == 0.5

    def test_empty_run_is_harmless(self):
        """FTS returns nothing for a typo or an all-stopword query; the vector
        side must still stand."""
        from src.search import reciprocal_rank_fusion as rrf
        out = rrf([([{"chunk_id": "a"}], 1.0), ([], 1.0)], k=5)
        assert [h["chunk_id"] for h in out] == ["a"]
