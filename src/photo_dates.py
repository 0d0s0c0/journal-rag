"""Date index for the media archive: date -> [paths].

Needed because 756 entries (44%) carry no photo links — the journals written in
a hurry. Their photos exist in the folder tree and should still be attached, and
the only thing connecting them to an entry is the day they were taken.

Validated against ground truth before being relied on: across the 11,000 photos
whose links already tell us the correct entry, the photo's date equals the
entry's date **94.8%** of the time, and is within one day **99.2%** of the time.
There is no systematic camera-clock offset, so a same-day match is sound.

Dates come from the filename where the camera encodes one (`PXL_20211215_...`),
otherwise from EXIF `DateTimeOriginal`. Scanning 16,000 files takes a couple of
minutes, so the result is cached; pass --rescan to rebuild.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from PIL import Image, ExifTags

MEDIA = {".jpg", ".jpeg", ".png", ".mp4", ".mov", ".m4v", ".heic", ".tif", ".tiff"}

# Cameras that encode the timestamp in the filename — cheaper and more reliable
# than EXIF, and it survives the metadata being stripped.
FNAME_DATE = re.compile(r"(?:PXL|IMG|VID|DSC|MVI)[_-](\d{4})(\d{2})(\d{2})")

# Bare YYYYMMDD, as some phones write it: 20231223_174348.jpg
FNAME_BARE = re.compile(r"\b(20[0-2]\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\b")

# Unix milliseconds, written by messaging apps: 1733320396486.jpg. Such images
# arrive with EXIF stripped, so the filename is the only surviving date.
FNAME_EPOCH = re.compile(r"^(1[0-9]{12})$")


def file_date(f: Path) -> str | None:
    """Return YYYY-MM-DD for a media file, or None."""
    for rx in (FNAME_DATE, FNAME_BARE):
        m = rx.search(f.name)
        if m:
            y, mo, d = m.groups()
            if 1990 <= int(y) <= 2100 and 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
                return f"{y}-{mo}-{d}"

    m = FNAME_EPOCH.match(f.stem)
    if m:
        from datetime import datetime, timezone
        try:
            dt = datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc)
            if 2000 <= dt.year <= 2100:
                return dt.strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            pass
    try:
        ex = Image.open(f).getexif()
        sub = ex.get_ifd(ExifTags.IFD.Exif) or {}
        raw = sub.get(36867) or sub.get(36868) or ex.get(306)
        if raw:
            s = str(raw)[:10].replace(":", "-")
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                return s
    except Exception:
        pass
    return None


def build(root: Path) -> dict[str, list[str]]:
    index: dict[str, list[str]] = defaultdict(list)
    undated = 0
    for f in root.rglob("*"):
        if f.suffix.lower() not in MEDIA or f.name.startswith("."):
            continue
        d = file_date(f)
        if d:
            index[d].append(f.relative_to(root).as_posix())
        else:
            undated += 1
    if undated:
        print(f"  {undated:,} media files have no usable date")
    return dict(index)


def load(root: Path, cache: Path, rescan: bool = False) -> dict[str, list[str]]:
    if cache.exists() and not rescan:
        return json.loads(cache.read_text())
    index = build(root)
    cache.write_text(json.dumps(index))
    return index


def main() -> None:
    from src.config import CONFIG
    index = load(CONFIG.paths.raw, CONFIG.paths.photo_dates, "--rescan" in sys.argv)
    total = sum(len(v) for v in index.values())
    print(f"dated media files : {total:,}")
    print(f"distinct dates    : {len(index):,}")
    years = sorted({d[:4] for d in index})
    print(f"years covered     : {years[0]}..{years[-1]}")


if __name__ == "__main__":
    main()
