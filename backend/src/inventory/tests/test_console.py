"""Drawing a record for a person, held on its own terms.

`inventory_tng.console` imports neither Django nor structlog, on purpose: the
same function draws a record on its way out of this process and redraws a saved
stream from another one. So these tests need nothing configured either, which
is the point being demonstrated as much as it is a convenience.

What is worth holding here is the part a reader would otherwise have to notice
for themselves — that a column keeps its width, that the informative half of a
logger name is the half that survives, and that piping to a file gives text
rather than escape codes.
"""

import io
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings

from inventory_tng import console

RECORD: dict[str, Any] = {
    "timestamp": "2026-08-23T14:32:07.412-04:00",
    "level": "warning",
    "logger": "inventory.sheet.batches",
    "event": "a batch was replayed",
    "request_id": "9f2c1a",
    "rows": 12,
}


def test_the_widest_layout_that_fits_is_the_one_chosen() -> None:
    assert console.choose(200) is console.FULL
    assert console.choose(140) is console.FULL
    assert console.choose(139) is console.COMPACT
    assert console.choose(100) is console.COMPACT
    assert console.choose(99) is console.MINIMAL
    assert console.choose(20) is console.MINIMAL


def test_a_layout_named_outright_beats_the_measurement() -> None:
    """Because a developer who has said what they want has said it."""
    assert console.choose(60, "full") is console.FULL
    assert console.choose(300, "minimal") is console.MINIMAL


def test_a_layout_that_does_not_exist_stops_rather_than_being_ignored() -> None:
    with pytest.raises(ValueError, match="compact"):
        console.choose(200, "tiny")


def test_the_announcement_names_the_width_the_layout_and_the_override() -> None:
    """The whole of the no-silent-adaptation rule, in one line.

    A developer must never have to wonder why their console differs from a
    colleague's, so the process says which layout it picked, what that layout
    costs them, and how to overrule it.
    """
    said = console.announcement(console.COMPACT, 118)

    assert "compact" in said
    assert "118" in said
    assert "140" in said, "it has to say what the next layout up would need"
    assert "module path" in said, "and what choosing this one drops"
    assert "DJANGO_LOG_LAYOUT=full" in said


def test_the_widest_layout_announces_nothing_at_all() -> None:
    """It drops nothing, so there is nothing a reader would be surprised by.

    Answered inside `announcement` rather than at each call site, because a
    guard held in two places is a guard that can be held in only one.
    """
    assert console.announcement(console.FULL, 200) == ""


def test_a_forced_layout_says_so_rather_than_blaming_the_terminal() -> None:
    assert "forced by DJANGO_LOG_LAYOUT=minimal" in console.announcement(console.MINIMAL, 200, "minimal")


def test_the_message_starts_in_the_same_column_whatever_the_record() -> None:
    """The property the fixed columns exist for: an eye can follow one down.

    A short logger and a long one have to leave the message in one place, or
    the column is not a column.
    """
    short = console.render({**RECORD, "logger": "django"}, console.FULL)
    long = console.render({**RECORD, "logger": "inventory.sheet.batches.replay"}, console.FULL)

    assert short.index("a batch was replayed") == long.index("a batch was replayed")


def test_a_long_logger_is_truncated_from_the_left() -> None:
    """The tail is the informative part.

    Told to keep one half of `inventory.sheet.batches`, a reader wants the
    second.
    """
    drawn = console.render({**RECORD, "logger": "inventory.sheet.batches.replay.rows.staged"}, console.FULL)

    assert "rows.staged" in drawn
    assert "…" in drawn


def test_the_two_loggers_this_work_is_built_around_are_not_cut() -> None:
    """The widest layout announces nothing, so anything it drops is silent.

    `django.security.DisallowedHost` is the logger from the incident decision
    0021 opens with, and `django.db.backends` is the documented example for
    per-logger levels. A saved stream grepped for either has to find it.
    """
    for logger in ("django.security.DisallowedHost", "django.db.backends.postgresql"):
        assert logger in console.render({**RECORD, "logger": logger}, console.FULL)


def test_a_compact_drawing_keeps_only_the_last_segment() -> None:
    drawn = console.render(RECORD, console.COMPACT)

    assert "batches" in drawn
    assert "inventory.sheet" not in drawn


def test_a_minimal_drawing_has_no_logger_column_at_all() -> None:
    drawn = console.render(RECORD, console.MINIMAL)

    assert "batches" not in drawn
    assert "a batch was replayed" in drawn


def test_a_narrow_layout_shortens_the_time_but_the_record_keeps_it() -> None:
    """Which characters are drawn is a drawing decision, made at draw time.

    If it were made when the record was written, a narrow terminal would
    produce a narrower record and what a collector received would depend on
    who happened to be watching.
    """
    assert "2026-08-23" in console.render(RECORD, console.FULL)
    assert "2026-08-23" not in console.render(RECORD, console.COMPACT)
    assert "14:32:07.412" in console.render(RECORD, console.COMPACT)


def test_keys_bound_for_every_record_are_left_out_by_default() -> None:
    """`request_id` on forty consecutive lines is noise, not information."""
    drawn = console.render(RECORD, console.FULL)

    assert "rows=12" in drawn
    assert "9f2c1a" not in drawn


def test_and_are_put_back_when_following_one_request_is_the_point() -> None:
    assert "9f2c1a" in console.render(RECORD, console.FULL, context=True)


def test_the_pairs_are_drawn_in_a_stable_order() -> None:
    """Sorted, for the reason `pairs` gives."""
    drawn = console.render({**RECORD, "zebra": 1, "apple": 2}, console.FULL)

    assert drawn.index("apple=") < drawn.index("rows=") < drawn.index("zebra=")


def test_a_traceback_goes_under_the_record_rather_than_beside_it() -> None:
    drawn = console.render({**RECORD, "exception": "Traceback (most recent call last):\n  ..."}, console.FULL)

    assert drawn.splitlines()[0].endswith("rows=12")
    assert "Traceback" in drawn.splitlines()[1]


def test_nesting_indents_until_it_stops_being_worth_it() -> None:
    """Sixteen columns of leading space tells a reader less than a number does."""
    assert console.nesting(0) == ""
    assert console.nesting(3) == "      "
    assert console.nesting(console.MAX_DEPTH) == "  " * console.MAX_DEPTH
    assert console.nesting(11).endswith("[11] ")


def test_colour_is_for_a_terminal_and_a_pipe_gets_plain_text() -> None:
    """`in_colour` says why a pipe must come out clean."""
    coloured = console.render(RECORD, console.FULL, colour=True)
    plain = console.render(RECORD, console.FULL, colour=False)

    assert "\033[" in coloured
    assert "\033[" not in plain


def test_no_colour_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """A terminal is not consent; NO_COLOR is the way to say so."""

    class Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    assert console.in_colour(Terminal()) is True

    monkeypatch.setenv("NO_COLOR", "1")
    assert console.in_colour(Terminal()) is False


def test_a_pipe_is_never_coloured_even_without_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert console.in_colour(io.StringIO()) is False


def test_a_json_stream_is_redrawn() -> None:
    lines = [json.dumps(RECORD), json.dumps({**RECORD, "event": "and another"})]

    drawn = list(console.redraw(lines, console.FULL))

    assert "a batch was replayed" in drawn[0]
    assert "and another" in drawn[1]


def test_a_line_that_is_not_a_record_passes_straight_through() -> None:
    """A real container's output is not pure JSON and never will be.

    An interpreter's dying traceback, and whatever a dependency wrote before
    logging was configured, both land in the same stream -- and those are
    exactly the lines somebody piping it through this is trying to read.
    """
    drawn = list(console.redraw(["[CRITICAL] gunicorn: worker failed to boot", "12", json.dumps(RECORD)], console.FULL))

    assert drawn[0] == "[CRITICAL] gunicorn: worker failed to boot"
    assert drawn[1] == "12", "a bare JSON number is not a record either"
    assert "a batch was replayed" in drawn[2]


def test_a_record_missing_everything_still_draws() -> None:
    """Redrawing a stream from another process means meeting anything at all."""
    assert console.render({}, console.FULL).strip() == "INFO"


# --------------------------------------------------------------------------
# The reader half, which is a program of its own.
# --------------------------------------------------------------------------


def test_reading_a_stream_draws_it_for_this_terminal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The point of rendering on read: the width is measured HERE.

    Which is the argument `terminal_width` makes, exercised end to end.
    """
    monkeypatch.setenv("DJANGO_LOG_LAYOUT", "full")
    monkeypatch.delenv("DJANGO_LOG_CONTEXT", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(RECORD) + "\n"))

    console.main()

    drawn = capsys.readouterr()

    assert "a batch was replayed" in drawn.out
    assert "2026-08-23" in drawn.out
    assert "9f2c1a" not in drawn.out, "bound keys stay hidden unless asked for"


def test_the_announcement_goes_to_standard_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """So it cannot land in the middle of the records on standard output.

    Somebody piping this into `grep` is piping standard output, and a line of
    prose in there is a line their pattern has to survive.
    """
    monkeypatch.setenv("DJANGO_LOG_LAYOUT", "minimal")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(RECORD) + "\n"))

    console.main()

    drawn = capsys.readouterr()

    assert "console layout: minimal" in drawn.err
    assert "console layout" not in drawn.out


def test_the_context_flag_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DJANGO_LOG_LAYOUT", "full")
    monkeypatch.setenv("DJANGO_LOG_CONTEXT", "shown")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(RECORD) + "\n"))

    console.main()

    assert "9f2c1a" in capsys.readouterr().out


def test_the_documented_pipeline_actually_runs() -> None:
    """`scripts/pretty-logs`, as a document tells somebody to run it.

    Held as a subprocess because everything this covers is outside Python: that
    the script is executable, that it finds an interpreter, and that it puts
    `src` on the path -- which it has to say for itself, because neither pytest
    nor `manage.py` is there to have said it.
    """
    script = Path(settings.BASE_DIR).parent.parent / "scripts" / "pretty-logs"

    finished = subprocess.run(
        [str(script)],
        input=json.dumps(RECORD) + "\nnot json at all\n",
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "DJANGO_LOG_LAYOUT": "full"},
        check=False,
    )

    assert finished.returncode == 0, finished.stderr
    assert "a batch was replayed" in finished.stdout
    assert "not json at all" in finished.stdout


# --------------------------------------------------------------------------
# Input this module does not control, which is most of it.
# --------------------------------------------------------------------------

ESCAPE = "\x1b"


def test_a_control_sequence_in_a_record_is_shown_and_not_obeyed() -> None:
    """The `event` of a `django.request` record is chosen by whoever made the
    request, because Django builds it from the path. So a URL is a way to write
    escape codes into the terminal of every operator who reads the stream --
    clearing their screen, recolouring it, or using a carriage return to
    overwrite what was already drawn.

    `pairs` has never had this problem, because `!r` escapes what it draws.
    """
    hostile = {**RECORD, "event": f"Not Found: /{ESCAPE}[2J{ESCAPE}[31mgone{ESCAPE}[0m\rGET /admin 200"}

    drawn = console.render(hostile, console.FULL)

    assert ESCAPE not in drawn
    assert "\r" not in drawn
    assert "gone" in drawn, "the text is shown, it is only the instructions that are dropped"


def test_the_same_holds_for_a_logger_a_timestamp_and_a_traceback() -> None:
    """Every field with a column of its own, not only the obvious one."""
    hostile = {
        "timestamp": f"2026-08-23T14:32:07.412-04:00{ESCAPE}[31m",
        "logger": f"inventory{ESCAPE}[2J",
        "event": "ordinary",
        "exception": f"Traceback{ESCAPE}[2J",
    }

    assert ESCAPE not in console.render(hostile, console.FULL)


def test_a_message_with_a_newline_stays_on_its_line() -> None:
    """`captureWarnings` hands Python's warnings over ending in one, and a
    newline in the middle of a message puts every column after it out of true.
    """
    drawn = console.render({**RECORD, "event": "first\nsecond"}, console.FULL)

    assert drawn.count("\n") == 0
    assert "first second" in drawn


def test_a_depth_that_is_not_a_number_does_not_take_the_record_with_it() -> None:
    """In-process that would drop the record through `handleError`, with only
    `--- Logging error ---` to show for it; in the reader it would end the
    stream mid-follow.
    """
    for bad in ("deep", {"a": 1}, None, [1]):
        assert "a batch was replayed" in console.render({**RECORD, "depth": bad}, console.FULL)


@pytest.mark.parametrize("prefix", ["backend-1  | ", "2026-08-23T18:32:07.412991827Z ", ""])
def test_a_line_with_something_in_front_of_the_record_still_draws(prefix: str) -> None:
    """`docker compose logs` writes `backend-1  | ` before every line, and
    `kubectl logs --timestamps` writes its own.

    Which made the pipeline four documents print a silent no-op: every record
    failed to parse and fell through to the pass-through branch, so what came
    back was the JSON the reader was trying to escape.
    """
    drawn = next(iter(console.redraw([prefix + json.dumps(RECORD)], console.FULL)))

    assert "a batch was replayed" in drawn
    assert "backend-1" not in drawn, "the prefix names a service the reader already chose"


def test_a_line_with_a_brace_and_no_record_is_still_passed_through() -> None:
    """Looking for a `{` must not turn a pass-through into a swallowed line."""
    assert list(console.redraw(["gunicorn: {not json} at all"], console.FULL)) == ["gunicorn: {not json} at all"]


def test_a_timestamp_written_by_something_else_does_not_ragged_the_columns() -> None:
    """`redraw` is documented to take a stream from anywhere, where seconds
    precision and a `Z` suffix are both entirely ordinary. A fixed slice cut
    `14:32:07+00:00` down to a trailing colon and moved the message column.
    """
    drawn = [
        console.render({**RECORD, "timestamp": when}, console.COMPACT)
        for when in ("2026-08-23T14:32:07.412-04:00", "2026-08-23T14:32:07+00:00", "2026-08-23T14:32:07Z", "")
    ]
    starts = {line.index("a batch was replayed") for line in drawn}

    assert len(starts) == 1, "the message column has to hold whatever the timestamp looks like"
    assert "+00:" not in drawn[1], "half an offset is worse than none"


def test_a_stack_goes_under_the_record_like_a_traceback() -> None:
    """`stack_info=True` produced one enormous line with literal escapes in it."""
    drawn = console.render({**RECORD, "stack": "Stack (most recent call last):\n  File ..."}, console.FULL)

    assert drawn.splitlines()[0].endswith("rows=12")
    assert drawn.splitlines()[1].startswith("Stack")


def test_a_key_the_writer_recorded_as_inherited_is_hidden_and_the_rest_is_not() -> None:
    """Provenance, not the name. `status` and `path` are ordinary words, and a
    field passed on this one call has to appear in both drawings or the two
    disagree about what was logged.
    """
    record = {**RECORD, "status": 500, console.BOUND_KEYS: ["request_id", "trace_id"], "trace_id": ""}

    drawn = console.render(record, console.FULL)

    assert "status=500" in drawn
    assert "9f2c1a" not in drawn
    assert "trace_id" not in drawn


def test_a_stream_that_never_said_falls_back_to_hiding_by_name() -> None:
    """A file saved before any of this, or written by something else."""
    assert "9f2c1a" not in console.render(RECORD, console.FULL)


def test_a_closed_pipe_ends_the_reader_without_a_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    """`| head`, `| grep -m1` and quitting `less` all close the pipe.

    The reasoning, and why standard output is redirected rather than simply
    left alone, is on `main`.
    """
    devnull: list[int] = []

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise BrokenPipeError

    monkeypatch.setenv("DJANGO_LOG_LAYOUT", "full")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(RECORD) + "\n"))
    monkeypatch.setattr(console, "redraw", explode)
    monkeypatch.setattr(console.os, "open", lambda *a, **k: 99)
    monkeypatch.setattr(console.os, "dup2", lambda source, target: devnull.append(source))

    class Pipe(io.StringIO):
        """A StringIO has no file descriptor; the real standard output does."""

        def fileno(self) -> int:
            return 1

    monkeypatch.setattr("sys.stdout", Pipe())

    console.main()

    assert devnull == [99], "standard output is redirected rather than left to raise at exit"
