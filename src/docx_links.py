"""Extract photo hyperlinks from journal .docx files.

Journals reference their photos via Word's Insert -> Link, which stores an
external hyperlink relationship in the .docx. Two things come out of this that
we want:

  1. the target path, which identifies the photo
  2. the paragraph it sits in, which identifies the passage it belongs to

(2) is the valuable half — it pairs text with image explicitly, rather than
guessing from timestamps.

Word stores whatever path it was given, so targets appear in several shapes:

    2019/Strelsau/IMG_1234.jpg
    file:///C:/Users/me/Journals/2019/Zenda%20Bay/IMG_9876.jpg
    ..\\2019\\Klow\\IMG_0001.jpg

We do not ask Word to resolve these. We parse them and keep the trailing
<year>/<place>/<filename>, which re-roots onto the local archive regardless of
what machine or drive letter the link was created on.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn

MEDIA_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".tif", ".tiff",
    ".mov", ".mp4", ".m4v", ".avi",
}


@dataclass
class PhotoLink:
    paragraph_index: int   # position in the document -> which entry it belongs to
    paragraph_text: str
    anchor_text: str       # the clickable text, occasionally a caption
    raw_target: str        # exactly what Word stored
    relative_path: str     # normalised <year>/<place>/<filename>


def _normalise(target: str) -> str:
    """Reduce any stored link target to <year>/<place>/<filename>.

    Handles URL-encoding, file:// prefixes, Windows drive letters and
    backslash separators. Returns the trailing three components, which is the
    archive's own layout and therefore re-rootable.
    """
    t = unquote(target).replace("file:///", "").replace("file://", "")

    if "\\" in t or (len(t) > 1 and t[1] == ":"):
        parts = PureWindowsPath(t).parts
    else:
        parts = PurePosixPath(t).parts

    # drop path roots ('/', '\\', 'C:') and relative hops
    parts = [p for p in parts if p not in ("/", "\\", "..", ".") and not p.endswith(":")]
    return "/".join(parts[-3:])


def photo_links(docx_path: str | Path) -> list[PhotoLink]:
    """Return every external media hyperlink in the document, in order."""
    doc = Document(str(docx_path))
    rels = doc.part.rels
    out: list[PhotoLink] = []

    for i, par in enumerate(doc.paragraphs):
        for h in par._p.findall(qn("w:hyperlink")):
            rid = h.get(qn("r:id"))
            if not rid or rid not in rels:
                continue                       # internal bookmark, not a file link
            rel = rels[rid]
            if rel.reltype != RT.HYPERLINK:
                continue

            raw = rel.target_ref
            if Path(unquote(raw)).suffix.lower() not in MEDIA_SUFFIXES:
                continue                       # a web link, not a photo

            anchor = "".join(t.text or "" for t in h.iter(qn("w:t")))
            out.append(
                PhotoLink(
                    paragraph_index=i,
                    paragraph_text=par.text,
                    anchor_text=anchor,
                    raw_target=raw,
                    relative_path=_normalise(raw),
                )
            )
    return out


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        sys.exit("usage: python -m src.docx_links <file.docx>")
    for link in photo_links(sys.argv[1]):
        print(f"para {link.paragraph_index}: {link.paragraph_text[:60]!r}")
        print(f"   anchor   : {link.anchor_text}")
        print(f"   raw      : {link.raw_target}")
        print(f"   resolved : {link.relative_path}\n")
