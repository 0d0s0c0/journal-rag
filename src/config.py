"""Single source of truth for paths and model settings.

Everything is read from config.yaml, which is gitignored — it holds the real
path to a private archive. config.example.yaml shows the shape.

    from src.config import CONFIG
    CONFIG.paths.raw
    CONFIG.embed.model

Before this existed, `Path.home()/"playground/journal-data"` was hardcoded in
five modules and the embedding model in two. Anyone cloning the repo had to
find and edit all of them, and a model name out of sync between the indexer and
the searcher produces an index that builds fine and returns nonsense.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("JOURNAL_CONFIG", REPO_ROOT / "config.yaml"))
EXAMPLE_PATH = REPO_ROOT / "config.example.yaml"

_VAR = re.compile(r"\$\{(\w+)\}")


@dataclass(frozen=True)
class Paths:
    data_root: Path
    raw: Path
    text: Path
    index: Path
    eval: Path
    facts: Path
    corrections: Path
    photo_dates: Path

    @property
    def entries(self) -> Path:
        return self.text / "entries.jsonl"

    @property
    def chunks(self) -> Path:
        return self.text / "chunks.jsonl"

    @property
    def manifest(self) -> Path:
        return self.text / "manifest.csv"

    @property
    def questions(self) -> Path:
        return self.eval / "questions.txt"


@dataclass(frozen=True)
class Embed:
    model: str
    dimensions: int
    query_instruction: str
    batch: int = 32

    def query_prompt(self, query: str) -> str:
        """Queries carry the instruction prefix. Documents never do."""
        return f"Instruct: {self.query_instruction}\nQuery: {query}"


@dataclass(frozen=True)
class Retrieval:
    top_k: int = 5
    min_score: float = 0.0
    relative_cutoff: float = 0.0


@dataclass(frozen=True)
class Chunking:
    max_chars: int = 2000
    min_chars: int = 120
    overlap_sentences: int = 1


@dataclass(frozen=True)
class Config:
    paths: Paths
    embed: Embed
    retrieval: Retrieval
    chunking: Chunking
    ollama_host: str
    chat_model: str
    raw: dict = field(default_factory=dict, repr=False)


def _expand(value: str, root: str) -> Path:
    """Resolve ${data_root} and ~ — neither is handled by plain YAML."""
    return Path(_VAR.sub(lambda m: root if m.group(1) == "data_root" else m.group(0),
                         value)).expanduser()


def load(path: Path = CONFIG_PATH) -> Config:
    """Load config.yaml, falling back to the example.

    Falling back rather than failing means a fresh clone runs — and reports a
    missing data directory with a real path — instead of dying on an
    AttributeError three imports deep, which is what returning None did.
    """
    if not path.exists():
        if not EXAMPLE_PATH.exists():
            raise SystemExit(f"no config at {path} and no {EXAMPLE_PATH.name}")
        print(f"note: no {path.name}; using {EXAMPLE_PATH.name} defaults."
              f"  cp {EXAMPLE_PATH.name} {path.name}  to customise.")
        path = EXAMPLE_PATH
    doc = yaml.safe_load(path.read_text()) or {}

    root_raw = str(doc.get("data_root", "~/journal-data"))
    data_root = Path(root_raw).expanduser()
    p = doc.get("paths", {})

    def sub(key: str, default: str) -> Path:
        return _expand(str(p.get(key, default)), str(data_root))

    paths = Paths(
        data_root=data_root,
        raw=sub("raw", "${data_root}/raw"),
        text=sub("text", "${data_root}/text"),
        index=sub("index", "${data_root}/index"),
        eval=sub("eval", "${data_root}/eval"),
        facts=sub("facts", "${data_root}/facts.db"),
        corrections=_expand(str(p.get("corrections",
                                      "${data_root}/corrections.txt")), str(data_root)),
        photo_dates=_expand(str(p.get("photo_dates",
                                      "${data_root}/photo_dates.json")), str(data_root)),
    )

    e = doc.get("embedding", {})
    o = doc.get("ollama", {})
    embed = Embed(
        model=o.get("embed_model", "qwen3-embedding:0.6b"),
        dimensions=int(e.get("dimensions", 1024)),
        query_instruction=(e.get("query_instruction") or
                           "Given a search query, retrieve relevant passages "
                           "from a personal travel journal").strip(),
        batch=int(e.get("batch", 32)),
    )

    r = doc.get("retrieval", {})
    c = doc.get("chunking", {})
    return Config(
        paths=paths,
        embed=embed,
        retrieval=Retrieval(
            top_k=int(r.get("top_k", 5)),
            min_score=float(r.get("min_score", 0.0)),
            relative_cutoff=float(r.get("relative_cutoff", 0.0)),
        ),
        chunking=Chunking(
            max_chars=int(c.get("max_chars", 2000)),
            min_chars=int(c.get("min_chars", 120)),
            overlap_sentences=int(c.get("overlap", 1)),
        ),
        ollama_host=o.get("host", "http://localhost:11434"),
        chat_model=o.get("chat_model", "gemma4:12b"),
        raw=doc,
    )


CONFIG = load()
