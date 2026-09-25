"""Converter rules, each one traceable to a real failure in the archive."""

import pytest

from src.convert import normalise, normalise_path, strip_photo_refs


class TestStripPhotoRefs:
    """Inline photo references are ~30% of all characters. Left in, they
    dominate the embeddings — one entry would embed largely as
    'vale temple first enclosure root jpg' repeated eleven times."""

    def test_single_ref_removed(self):
        text = "We crossed the Old Bridge (strelsau\\old bridge.jpg), the oldest bridge."
        clean, refs = strip_photo_refs(text)
        assert ".jpg" not in clean
        assert "Old Bridge" in clean and "oldest bridge" in clean
        assert len(refs) == 1

    def test_comma_separated_run_removed_as_one(self):
        text = ("roots spilling over (2013\\syldavia\\root.jpg, 2013\\syldavia\\root2.jpg, "
                "2013\\syldavia\\root3.jpg) and carvings.")
        clean, refs = strip_photo_refs(text)
        assert ".jpg" not in clean
        assert "roots spilling over" in clean and "and carvings." in clean
        assert "(" not in clean and ")" not in clean

    def test_forward_slashes_and_spaces(self):
        clean, refs = strip_photo_refs("waited ( 2013/syldavia/vale temple - moat.jpg) there")
        assert ".jpg" not in clean and len(refs) == 1

    def test_video_refs_too(self):
        clean, _ = strip_photo_refs("the show (2013\\syldavia\\circus.mp4) was great")
        assert ".mp4" not in clean and "was great" in clean

    def test_text_without_refs_untouched(self):
        text = "A hearty breakfast served in our bedroom consisting with a lot of bread."
        clean, refs = strip_photo_refs(text)
        assert clean == text and refs == []


class TestNormalisePath:
    """Word stored whatever path it was given. We parse rather than resolve, so
    a stale C:\\ path from a machine that no longer exists still re-roots."""

    @pytest.mark.parametrize("raw,expected", [
        ("2019/Strelsau/IMG_1234.jpg", "2019/Strelsau/IMG_1234.jpg"),
        ("2013\\syldavia\\root.jpg", "2013/syldavia/root.jpg"),
        ("file:///C:/Users/me/Journals/2019/Zenda%20Bay/IMG_9876.jpg",
         "2019/Zenda Bay/IMG_9876.jpg"),
        ("..\\2019\\Klow\\IMG_0001.jpg", "2019/Klow/IMG_0001.jpg"),
        ("strelsau\\old bridge.jpg", "strelsau/old bridge.jpg"),   # no year
    ])
    def test_shapes(self, raw, expected):
        assert normalise_path(raw) == expected


class TestNormalise:
    def test_double_spacing_collapsed(self):
        assert normalise("One sentence.  Another one.") == "One sentence. Another one."

    def test_paragraph_breaks_kept(self):
        assert normalise("First para.\n\n\nSecond para.") == "First para.\n\nSecond para."

    def test_leading_tab_stripped(self):
        assert normalise("\tKlow") == "Klow"


class TestYearRollover:
    """A file named '<place> - YYYY' carries the year the trip STARTED.
    Dec->Jan travel is common, so the year must increment — but only at a real
    New Year. An earlier version incremented on any backwards month step, which
    a two-trip file triggered, and because the year carries forward it misdated
    every entry after it (25 entries landed in a year that had not happened).
    """

    @staticmethod
    def _roll(prev_month, month, year, base_year):
        crosses = prev_month >= 11 and month <= 2
        within = year + 1 <= base_year + 1
        return year + 1 if (crosses and within) else year

    def test_december_to_january_rolls(self):
        assert self._roll(12, 1, 2021, 2021) == 2022

    def test_november_to_february_rolls(self):
        assert self._roll(11, 2, 2021, 2021) == 2022

    def test_july_to_june_does_not_roll(self):
        """The zenda-2026 case: a mistyped month, not a New Year."""
        assert self._roll(7, 6, 2026, 2026) == 2026

    def test_october_to_june_does_not_roll(self):
        assert self._roll(10, 6, 2012, 2012) == 2012

    def test_cannot_roll_twice(self):
        """One file spans at most one New Year."""
        assert self._roll(12, 1, 2014, 2013) == 2014


class TestDashAutocorrect:
    """Word converts " - " to " – " as you type, so an inline text reference can
    carry an en dash where the file on disk has a hyphen. The hyperlink keeps
    the real name; only the prose is mangled. Three such references existed in
    the archive and all three failed to resolve."""

    def test_en_dash_path_falls_back_to_hyphen(self, tmp_path):
        from src.convert import MediaIndex
        d = tmp_path / "2025" / "syldavia"
        d.mkdir(parents=True)
        (d / "linden garden - bridge.jpg").write_bytes(b"x")
        idx = MediaIndex(tmp_path)

        hit, how = idx.resolve("2025/syldavia/linden garden – bridge.jpg", 2025)
        assert how == "dash_fix"
        assert hit == "2025/syldavia/linden garden - bridge.jpg"

    def test_em_dash_too(self, tmp_path):
        from src.convert import MediaIndex
        d = tmp_path / "2025" / "syldavia"
        d.mkdir(parents=True)
        (d / "a - b.jpg").write_bytes(b"x")
        assert MediaIndex(tmp_path).resolve("2025/syldavia/a — b.jpg", 2025)[1] == "dash_fix"

    def test_genuinely_missing_still_reports_missing(self, tmp_path):
        from src.convert import MediaIndex
        (tmp_path / "2025").mkdir()
        assert MediaIndex(tmp_path).resolve("2025/syldavia/nope – x.jpg", 2025)[1] == "missing"


class TestPhotoFilenameDates:
    """Some photos carry their date only in the filename — EXIF is stripped by
    messaging apps, and some phones never write DateTimeOriginal at all."""

    def test_bare_yyyymmdd(self, tmp_path):
        from src.photo_dates import file_date
        f = tmp_path / "20231223_174348.jpg"; f.write_bytes(b"")
        assert file_date(f) == "2023-12-23"

    def test_epoch_milliseconds(self, tmp_path):
        """1733320396486 -> 2024-12-04. Written by messaging apps; note this is
        the SAVE time, not necessarily when the photo was taken."""
        from src.photo_dates import file_date
        f = tmp_path / "1733320396486.jpg"; f.write_bytes(b"")
        assert file_date(f) == "2024-12-04"

    def test_implausible_numbers_rejected(self, tmp_path):
        from src.photo_dates import file_date
        for name in ("1234567890123456.jpg", "99999999.jpg", "20239999_1.jpg"):
            f = tmp_path / name; f.write_bytes(b"")
            assert file_date(f) is None, name
