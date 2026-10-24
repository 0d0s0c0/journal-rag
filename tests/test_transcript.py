"""Tests for the conversation transcript extractor.

The risk here is silent: a filter that stops matching produces a transcript that
looks fine while attributing injected system text to the user, or dropping real
turns. Nothing downstream would complain.
"""

from __future__ import annotations

import json

from src.transcript import (extract, is_real_user_turn, message_text,
                            project_logs, render)


def write_log(path, events):
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def user(text):
    return {"type": "user", "message": {"content": text}}


def assistant(*blocks):
    return {"type": "assistant", "message": {"content": list(blocks)}}


def text(s):
    return {"type": "text", "text": s}


class TestRealUserTurn:
    def test_a_plain_message_is_the_user(self):
        assert is_real_user_turn("where did we leave off?")

    def test_injected_messages_are_not(self):
        # Each of these actually appeared as a user-role message during this
        # project; attributing them to the user misreads the conversation.
        for s in ("<system-reminder>be careful</system-reminder>",
                  "This session is being continued from a previous conversation",
                  "[Request interrupted by user]",
                  "[SYSTEM NOTIFICATION - NOT USER INPUT]",
                  "<task-notification>done</task-notification>",
                  "The user doesn't want to proceed with this tool use.",
                  "Your questions have been answered: ..."):
            assert not is_real_user_turn(s), s

    def test_a_marker_further_in_still_counts(self):
        # Reminders are appended to otherwise-real text; the marker is not
        # always at position zero.
        s = "ok\n\n<system-reminder>context</system-reminder>"
        assert not is_real_user_turn(s)

    def test_a_long_message_mentioning_a_marker_late_is_kept(self):
        # Only the first 200 characters are inspected, so a user genuinely
        # discussing the mechanism is not silenced.
        s = "x" * 400 + "<system-reminder>"
        assert is_real_user_turn(s)


class TestMessageText:
    def test_plain_string(self):
        assert message_text({"content": "hello"}) == "hello"

    def test_text_blocks_are_joined(self):
        assert message_text({"content": [text("a"), text("b")]}) == "a\nb"

    def test_tool_and_reasoning_blocks_are_ignored(self):
        msg = {"content": [{"type": "thinking", "thinking": "hmm"},
                           {"type": "tool_use", "name": "Bash", "input": {}},
                           text("the reply")]}
        assert message_text(msg) == "the reply"

    def test_missing_content(self):
        assert message_text({}) == ""


class TestExtract:
    def test_pairs_prompts_with_replies(self, tmp_path):
        log = tmp_path / "a.jsonl"
        write_log(log, [user("first"), assistant(text("reply one")),
                        user("second"), assistant(text("reply two"))])
        turns, skipped = extract([log])
        assert [t["prompt"] for t in turns] == ["first", "second"]
        assert [t["reply"] for t in turns] == [["reply one"], ["reply two"]]
        assert skipped == 0

    def test_several_reply_blocks_belong_to_one_turn(self, tmp_path):
        # A reply interleaved with tool calls arrives as separate text blocks.
        log = tmp_path / "a.jsonl"
        write_log(log, [user("q"), assistant(text("before")),
                        assistant(text("after"))])
        turns, _ = extract([log])
        assert len(turns) == 1
        assert turns[0]["reply"] == ["before", "after"]

    def test_injected_messages_are_counted_not_attributed(self, tmp_path):
        log = tmp_path / "a.jsonl"
        write_log(log, [user("real"), user("<system-reminder>x</system-reminder>"),
                        assistant(text("r"))])
        turns, skipped = extract([log])
        assert len(turns) == 1 and skipped == 1
        # The reply must still attach to the real turn, not be lost with it.
        assert turns[0]["reply"] == ["r"]

    def test_shell_commands_are_marked(self, tmp_path):
        log = tmp_path / "a.jsonl"
        write_log(log, [user("<bash-input>ls -la</bash-input>")])
        turns, _ = extract([log])
        assert turns[0]["shell"] and turns[0]["prompt"] == "ls -la"

    def test_logs_are_read_in_order(self, tmp_path):
        a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
        write_log(a, [user("earlier")])
        write_log(b, [user("later")])
        turns, _ = extract([a, b])
        assert [t["prompt"] for t in turns] == ["earlier", "later"]

    def test_a_truncated_final_line_is_survivable(self, tmp_path):
        # The log is being appended to while this runs.
        log = tmp_path / "a.jsonl"
        log.write_text(json.dumps(user("good")) + "\n" + '{"type": "user", "mess')
        turns, _ = extract([log])
        assert [t["prompt"] for t in turns] == ["good"]

    def test_assistant_text_before_any_prompt_is_dropped(self, tmp_path):
        log = tmp_path / "a.jsonl"
        write_log(log, [assistant(text("orphan")), user("q")])
        turns, _ = extract([log])
        assert len(turns) == 1 and turns[0]["reply"] == []


class TestRender:
    def test_marks_an_interrupted_turn(self):
        out = render([{"prompt": "q", "shell": False, "reply": []}], 1)
        assert "interrupted" in out

    def test_does_not_mark_a_shell_command_as_interrupted(self):
        out = render([{"prompt": "ls", "shell": True, "reply": []}], 1)
        assert "interrupted" not in out
        assert "ran in the shell" in out

    def test_warns_that_the_file_is_private(self):
        out = render([{"prompt": "q", "shell": False, "reply": ["a"]}], 1)
        assert "never be committed" in out


class TestProjectLogs:
    def test_missing_directory_is_not_an_error(self, tmp_path):
        assert project_logs(tmp_path / "nope") == []
