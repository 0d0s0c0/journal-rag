"""Rebuild entries for trips whose journal is lost, from the photographs.

    uv run python -m src.reconstruct              # -> text/entries-reconstructed.jsonl
    uv run python -m src.reconstruct --dry-run
    uv run python -m src.reconstruct --list       # show orphaned folders only

Some media folders have no journal at all. Where those photographs were
*renamed* — "black canyon - gunnison river", "cody - old trail town saloon" —
a journal once existed: the renaming was done in order to reference them from
the prose. The writing is gone; the itinerary and the subjects are not.

What survives is enough to rebuild something useful:

  * the date, from EXIF or the filename
  * the place, from the folder
  * what was photographed, from the filenames — captions written at the time,
    by the person who was there

The result is NOT prose and must never be mistaken for it. Every reconstructed
entry carries `reconstructed` in its warnings, is written to a separate file,
and its text opens by saying what it is. A search that surfaces one should make
plain that these are photograph captions, not something that was written.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from src.config import CONFIG
from src.convert import Entry
from src.photo_dates import file_date

MEDIA = {".jpg", ".jpeg", ".png", ".mp4", ".mov", ".m4v", ".heic"}

# Camera defaults carry no meaning: IMG_1234, DSC00012, 20231223_174348,
# 1733320396486. Only hand-given names are worth keeping as captions.
CAMERA_NAME = re.compile(
    r"^(IMG|DSC|DSCN|PXL|VID|MVI|P\d|GOPR|SAM|PICT)([-_ ]?\d+)+$|^\d{8,}([-_]\d+)?$",
    re.IGNORECASE,
)

# "black canyon2" and "black canyon3" are the same subject photographed twice.
TRAILING_INDEX = re.compile(r"[ _-]?\d+$")


def caption(path: Path) -> str | None:
    """A usable caption from a filename, or None if it is a camera default."""
    stem = path.stem.strip()
    if not stem or CAMERA_NAME.match(stem):
        return None
    base = TRAILING_INDEX.sub("", stem).strip(" -_")
    return base or None


def orphaned_folders(root: Path, linked: set[str]) -> dict[str, list[Path]]:
    """Media folders that no entry references, keyed by relative folder path."""
    out: dict[str, list[Path]] = defaultdict(list)
    for f in root.rglob("*"):
        if f.suffix.lower() not in MEDIA or f.name.startswith("."):
            continue
        rel = f.relative_to(root).as_posix()
        folder = rel.rsplit("/", 1)[0]
        if folder not in linked:
            out[folder].append(f)
    return dict(out)


def build(folder: str, files: list[Path], root: Path) -> list[Entry]:
    """One entry per day photographed in this folder."""
    parts = folder.split("/")
    year = parts[0] if re.fullmatch(r"\d{4}", parts[0]) else None
    place = parts[-1]
    slug = re.sub(r"[^a-z0-9]+", "-", f"{place}-{year or 'undated'}".lower()).strip("-")

    # Entries are keyed by day, so a photograph with no parseable date cannot
    # be placed and is dropped rather than guessed at. The count is reported so
    # the loss is visible — in this archive it is small (59 of 979), because the
    # hand-named files almost always carry EXIF too.
    by_day: dict[str, list[Path]] = defaultdict(list)
    for f in files:
        d = file_date(f)
        if d:
            by_day[d].append(f)

    entries: list[Entry] = []
    for i, (day, group) in enumerate(sorted(by_day.items()), start=1):
        caps: list[str] = []
        for f in sorted(group):
            c = caption(f)
            if c and c not in caps:
                caps.append(c)

        if caps:
            text = (f"[Reconstructed from {len(group)} photographs — the journal "
                    f"for this trip is missing. These are the photograph captions, "
                    f"not written prose.]\n\n"
                    f"Photographed in {place}: " + "; ".join(caps) + ".")
        else:
            # No hand-given names: nothing to say beyond that it happened.
            text = (f"[Reconstructed: {len(group)} photographs taken in {place}. "
                    f"No journal and no captions survive for this day.]")

        entries.append(Entry(
            id=f"{slug}#r{i:04d}",
            source_file=f"(reconstructed: {folder})",
            trip=place,
            entry_date=day,
            month_day=day[5:],
            text=text,
            photos=[{"path": f.relative_to(root).as_posix(),
                     "source": "reconstructed", "resolved": "exact"}
                    for f in sorted(group)],
            chars=len(text),
            warnings=["reconstructed"] + ([] if caps else ["no_captions"]),
        ))
    return entries


def main() -> None:
    args = sys.argv[1:]
    paths = CONFIG.paths
    if not paths.entries.exists():
        sys.exit(f"missing {paths.entries} — run src.convert first")

    existing = [json.loads(l) for l in paths.entries.open()]
    linked = {p["path"].rsplit("/", 1)[0]
              for e in existing for p in e["photos"] if "/" in p["path"]}

    folders = orphaned_folders(paths.raw, linked)
    if not folders:
        print("no orphaned media folders — nothing to reconstruct")
        return

    print(f"orphaned media folders: {len(folders)}")
    all_entries: list[Entry] = []
    for folder, files in sorted(folders.items()):
        entries = build(folder, files, paths.raw)
        named = sum(1 for f in files if caption(f))
        dated = sum(1 for f in files if file_date(f))
        print(f"  {folder:<32} {len(files):4d} files  {named:4d} named  "
              f"{dated:4d} dated  -> {len(entries)} entries")
        all_entries.extend(entries)

    if "--list" in args:
        return

    print(f"\nreconstructed entries : {len(all_entries)}")
    print(f"photographs covered   : {sum(len(e.photos) for e in all_entries):,}")
    undated = sum(len(f) for f in folders.values()) - sum(len(e.photos) for e in all_entries)
    if undated:
        print(f"photographs with no date, not covered: {undated}")

    if "--dry-run" in args:
        print("\n--dry-run: nothing written")
        return

    out = paths.text / "entries-reconstructed.jsonl"
    with out.open("w") as fh:
        for e in sorted(all_entries, key=lambda e: e.entry_date or ""):
            fh.write(json.dumps(asdict(e), ensure_ascii=False) + "\n")
    print(f"\nwrote {out}")
    print("These are kept in a separate file so they can never be confused with\n"
          "entries that were actually written. Run src.chunk to include them.")


if __name__ == "__main__":
    main()
