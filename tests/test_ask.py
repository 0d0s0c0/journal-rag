"""Tests for the answering layer that do not need a model running.

Deliberately no live generation here: a test that needs Ollama up, both models
resident and 10 seconds per case is a test nobody runs. What is covered is the
logic that decides what the model is shown and how its reply is classified —
which is where the bugs in this file actually were.
"""

from __future__ import annotations

import io
import json

import pytest

from src.ask import REFUSAL, build_context, generate


def hit(date: str, text: str) -> dict:
    return {"entry_date": date, "text": text, "chunk_id": f"{date}#0",
            "entry_id": date}


class TestBuildContext:
    def test_keeps_everything_when_it_fits(self):
        hits = [hit("2019-01-04", "a" * 50), hit("2019-01-05", "b" * 50)]
        ctx, kept = build_context(hits, 10_000)
        assert len(kept) == 2
        assert "2019-01-04" in ctx and "2019-01-05" in ctx

    def test_drops_whole_excerpts_rather_than_truncating(self):
        # Half an excerpt with no marker is worse than one fewer excerpt: the
        # model cannot tell that it is reading a fragment.
        hits = [hit("2019-01-04", "a" * 400), hit("2019-01-05", "b" * 400)]
        ctx, kept = build_context(hits, 500)
        assert len(kept) == 1
        assert "b" * 400 not in ctx
        assert ctx.count("a") == 400        # the one that was kept is intact

    def test_always_keeps_at_least_one_even_if_over_budget(self):
        # Returning nothing would turn a tight budget into a silent refusal.
        hits = [hit("2019-01-04", "a" * 5000)]
        ctx, kept = build_context(hits, 100)
        assert len(kept) == 1 and ctx

    def test_labels_each_excerpt_with_its_date(self):
        ctx, _ = build_context([hit("2019-08-19", "text")], 10_000)
        assert ctx.startswith("[2019-08-19]")


class TestRefusalDetection:
    @pytest.mark.parametrize("answer", [
        "I can't find that in the journals.",
        "I cant find that in the journals",
        "That is not in the journals.",
        "The excerpts do not contain the answer.",
        "There is no mention of it.",
    ])
    def test_recognises_refusals(self, answer):
        assert REFUSAL.search(answer)

    @pytest.mark.parametrize("answer", [
        "You ate a quenelle on 2019-04-11.",
        "The journals record two such occasions (2019-08-19).",
    ])
    def test_does_not_flag_real_answers(self, answer):
        assert not REFUSAL.search(answer)


class TestGenerateGuards:
    """The thinking-mode failure: tokens generated, nothing returned."""

    @staticmethod
    def _fake_stream(monkeypatch, lines):
        body = b"".join(json.dumps(o).encode() + b"\n" for o in lines)

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr("urllib.request.urlopen",
                            lambda *a, **k: Resp(body))

    def test_raises_when_tokens_generated_but_no_text(self, monkeypatch):
        # This is exactly what was observed: 6,599 tokens, empty response, and
        # no `thinking` field either. Silently returning "" made it look like a
        # refusal, which is the opposite of what happened.
        self._fake_stream(monkeypatch, [{"done": True, "eval_count": 6599}])
        with pytest.raises(RuntimeError, match="6599 tokens but returned no text"):
            generate("prompt", 8192)

    def test_accepts_a_genuinely_empty_run(self, monkeypatch):
        # No tokens generated at all is a different situation and not an error.
        self._fake_stream(monkeypatch, [{"done": True, "eval_count": 0}])
        text, meta = generate("prompt", 8192)
        assert text == "" and meta["eval_count"] == 0

    def test_assembles_streamed_tokens_in_order(self, monkeypatch):
        self._fake_stream(monkeypatch, [
            {"response": "Que"}, {"response": "nelle"}, {"response": " in 2019."},
            {"done": True, "eval_count": 3, "prompt_eval_count": 120},
        ])
        text, meta = generate("prompt", 8192)
        assert text == "Quenelle in 2019."
        assert meta["prompt_eval_count"] == 120

    def test_forwards_each_token_to_the_callback(self, monkeypatch):
        self._fake_stream(monkeypatch, [
            {"response": "a"}, {"response": "b"}, {"done": True, "eval_count": 2},
        ])
        seen: list[str] = []
        generate("prompt", 8192, on_token=seen.append)
        assert seen == ["a", "b"]

    def test_thinking_is_off_by_default(self, monkeypatch):
        captured: dict = {}

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, *a, **k):
            captured.update(json.loads(req.data))
            return Resp(json.dumps({"done": True, "response": "ok",
                                    "eval_count": 1}).encode() + b"\n")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        generate("prompt", 8192)
        assert captured["think"] is False
        assert captured["stream"] is True
        assert captured["options"]["num_ctx"] == 8192
