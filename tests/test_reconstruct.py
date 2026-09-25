"""Rebuilding lost journals from photograph filenames.

Where photos were renamed by hand, a journal once existed — the renaming was
done so the prose could reference them. The writing is gone; the itinerary and
subjects survive in the filenames.

The overriding rule is honesty: a reconstruction must never be mistakable for
something that was written.
"""

from pathlib import Path

import pytest

from src.reconstruct import CAMERA_NAME, build, caption


class TestCaption:
    @pytest.mark.parametrize("name", [
        "IMG_1234.jpg", "DSC00012.jpg", "PXL_20211215_170951850.jpg",
        "20231223_174348.jpg", "1733320396486.jpg", "P1010101.jpg",
    ])
    def test_camera_defaults_yield_nothing(self, name):
        assert caption(Path(name)) is None

    @pytest.mark.parametrize("name,expected", [
        ("silver canyon - kestrel river.jpg", "silver canyon - kestrel river"),
        ("brandt - old trail town saloon.jpg", "brandt - old trail town saloon"),
        ("yellowstone - lower falls.jpg", "yellowstone - lower falls"),
    ])
    def test_hand_written_names_are_captions(self, name, expected):
        assert caption(Path(name)) == expected

    def test_repeat_shots_collapse_to_one_subject(self):
        """"black canyon2" and "black canyon3" are the same subject."""
        assert caption(Path("black canyon.jpg")) == "black canyon"
        assert caption(Path("black canyon2.jpg")) == "black canyon"
        assert caption(Path("black canyon3.jpg")) == "black canyon"


class TestBuild:
    def _folder(self, tmp_path, names, day="20160921"):
        d = tmp_path / "2016" / "elbonia"
        d.mkdir(parents=True)
        files = []
        for i, n in enumerate(names):
            f = d / f"{n}.jpg"
            f.write_bytes(b"")
            files.append(f)
        return d, files

    def test_entry_declares_itself_reconstructed(self, tmp_path):
        d, files = self._folder(tmp_path, ["20160921_101010", "20160921_101011"])
        entries = build("2016/elbonia", files, tmp_path)
        assert entries, "expected an entry"
        e = entries[0]
        assert "reconstructed" in e.warnings
        assert "Reconstructed" in e.text
        assert e.source_file.startswith("(reconstructed")

    def test_captions_become_the_text(self, tmp_path):
        """A caption survives only if that file also has a parseable date —
        entries are keyed by day, so an undated photo cannot be placed."""
        d = tmp_path / "2016" / "elbonia"; d.mkdir(parents=True)
        files = []
        for n in ["20160921 yellowstone - lower falls",
                  "20160921 yellowstone - buck lake"]:
            f = d / f"{n}.jpg"; f.write_bytes(b""); files.append(f)
        entries = build("2016/elbonia", files, tmp_path)
        text = " ".join(e.text for e in entries)
        assert "lower falls" in text and "buck lake" in text

    def test_captioned_but_undated_photo_is_dropped_not_guessed(self, tmp_path):
        d = tmp_path / "2016" / "elbonia"; d.mkdir(parents=True)
        f = d / "yellowstone - lower falls.jpg"; f.write_bytes(b"")
        assert build("2016/elbonia", [f], tmp_path) == []

    def test_no_captions_is_flagged_and_says_so(self, tmp_path):
        d, files = self._folder(tmp_path, ["IMG_0001", "IMG_0002"])
        # give them a parseable date
        for f in files:
            f.unlink()
        files = []
        for n in ["20160921_101010", "20160921_101011"]:
            f = d / f"{n}.jpg"; f.write_bytes(b""); files.append(f)
        entries = build("2016/elbonia", files, tmp_path)
        e = entries[0]
        assert "no_captions" in e.warnings
        assert "No journal and no captions survive" in e.text

    def test_one_entry_per_day(self, tmp_path):
        d = tmp_path / "2016" / "elbonia"; d.mkdir(parents=True)
        files = []
        for n in ["20160921_101010", "20160921_120000", "20160922_101010"]:
            f = d / f"{n}.jpg"; f.write_bytes(b""); files.append(f)
        entries = build("2016/elbonia", files, tmp_path)
        assert len(entries) == 2
        assert sorted(e.entry_date for e in entries) == ["2016-09-21", "2016-09-22"]

    def test_undated_photos_are_skipped_not_guessed(self, tmp_path):
        d = tmp_path / "2016" / "elbonia"; d.mkdir(parents=True)
        f = d / "some name with no date.jpg"; f.write_bytes(b"")
        assert build("2016/elbonia", [f], tmp_path) == []
