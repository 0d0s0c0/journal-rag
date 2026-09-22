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
