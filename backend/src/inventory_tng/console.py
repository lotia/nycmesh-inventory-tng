"""Drawing a log record for a person, in fixed columns.

Two callers, one implementation, which is the whole point. `logs.py` uses
`render` as structlog's last processor when the process is writing for a
terminal; `python -m inventory_tng.console` uses the same function to redraw a
JSON stream that was written for a collector. There is one format in the system
and one renderer, so what a developer reads is never a second approximation of
what was recorded.

Nothing here imports Django or structlog. The reader half has to run against a
stream from any process, including one on another machine, and a renderer that
needed the application configured would not.

WHAT THE COLUMNS ARE FOR. The eye follows one column down a page and cannot
follow a field that moves. So the timestamp, the level and the logger each have
a width, the message always starts in the same place, and everything specific
to one record goes after it as `key=value`. The logger is truncated from the
LEFT because the informative half of `inventory.sheet.batches` is `batches`.

WHAT IT WILL NOT DO is change shape without saying so. `announcement` is a line
the process prints once at startup naming the width it measured, the layout it
chose, what that layout drops and the variable that overrides it. A developer
should never have to wonder why their console differs from a colleague's.
"""

import json
import os
import re
import shutil
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any, TextIO

from inventory_tng.options import setting

# Keys every record carries because something bound them for the life of a
# request or a process. Repeating `request_id=...` on forty consecutive lines
# is noise, so the console leaves them out and one summary line carries them.
# DJANGO_LOG_CONTEXT=shown puts them back, which is what you want exactly once,
# when you are chasing a correlation rather than reading a story.
BOUND = frozenset(
    {
        "request_id",
        "trace_id",
        "span_id",
        "method",
        "path",
        "status",
        "user",
        "process",
        "service",
    }
)

# The exact set of keys a record inherited rather than stated, written by
# `logs.context` and carried in the record itself -- including as JSON. That
# last part is the whole point: the reader is what draws a compose or cluster
# stream, so a record that kept its provenance to itself would leave the
# reader guessing by name and hiding a `status` its writer would have shown.
# Two drawings, two answers, which is the thing decision 0021 forbids.
#
# BOUND below is the guess, and it is only for a stream that predates this or
# came from somewhere else.
BOUND_KEYS = "bound"

# Never drawn as `key=value`: each one has a column of its own, is drawn under
# the record rather than beside it, or is machinery.
STRUCTURAL = frozenset({"timestamp", "level", "logger", "event", "exception", "positional_args", BOUND_KEYS, "depth"})

# Drawn UNDER the record rather than beside it, when they carry text. A
# traceback and a captured stack are many lines each.
#
# `stack_info` is not in STRUCTURAL with them, and that is the whole subtlety.
# Whether it is a block depends on its VALUE, not on its name: a stdlib record
# through `ProcessorFormatter` arrives with the formatted stack as a string,
# and a structlog logger -- which is every logger this application uses --
# leaves `stack_info=True` in the event dict, because `StackInfoRenderer` is
# deliberately absent from the chain (`logs.FROM_LIBRARIES` says why). Naming
# it structural drew that boolean as a bare line reading `True`, having removed
# the `stack_info=True` pair that at least said what it was.
#
# So the string is a block, and anything else is an ordinary pair.
# inventory-tng-0qys, and inventory-tng-dke1 for the stack that is never
# captured at all.
UNDER = ("stack_info", "exception")

# Everything a terminal acts on rather than shows: escape sequences move the
# cursor, recolour, and clear the screen, and a carriage return overwrites the
# line just drawn. A record's `event` can be attacker-chosen -- Django builds
# one from the request path -- so it reaches this module as bytes to display
# and never as bytes to obey.
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

# Two spaces per level of call nesting, and a number past this. Nesting stops
# being countable by eye at about six, so an eighth level of indentation buys
# nothing and costs sixteen columns that the message needed.
MAX_DEPTH = 8

LEVEL_WIDTH = 8

# 8-colour SGR only. A log stream is read over ssh, in tmux, and in whatever
# terminal a volunteer already had open, and 256-colour is not universally
# safe there in a way that buys anything here.
COLOURS = {
    "critical": "\033[1;31m",
    "error": "\033[31m",
    "warning": "\033[33m",
    "info": "\033[32m",
    "debug": "\033[36m",
}
DIM = "\033[2m"
RESET = "\033[0m"


@dataclass(frozen=True)
class Layout:
    """One arrangement of the columns, and what choosing it gives up."""

    name: str
    # The narrowest terminal this layout is willing to draw into. Chosen from
    # what the columns cost before the message starts: a full ISO-8601
    # timestamp with an offset is 29 characters on its own.
    needs: int
    # `iso` is 2026-08-23T14:32:07.412-04:00; `time` is 14:32:07.412.
    timestamp: str
    # Width of the logger column, or None for a layout that has no room for one.
    logger: int | None
    # `whole` keeps the dotted path, truncated from the left; `tail` keeps only
    # the last segment.
    logger_style: str
    # Named in the startup announcement, so the developer is told what they are
    # not seeing rather than left to notice. Empty means there is nothing to
    # tell them, which is the same question `announcement` answers -- so it is
    # asked there once rather than at every call site.
    drops: str


# 34 columns because the two logger names this work is built around --
# django.security.DisallowedHost and django.db.backends.postgresql -- are 30
# and 29, and a layout that drops nothing must not silently cut the name in
# decision 0021's own motivating incident. Longer names are still cut, from
# the left; DEVELOPERS.md says so rather than leaving it to be discovered.
FULL = Layout(name="full", needs=140, timestamp="iso", logger=34, logger_style="whole", drops="")
COMPACT = Layout(
    name="compact",
    needs=100,
    timestamp="time",
    logger=12,
    logger_style="tail",
    drops="the date and offset, and the logger's module path",
)
MINIMAL = Layout(
    name="minimal",
    needs=0,
    timestamp="time",
    logger=None,
    logger_style="tail",
    drops="the date and offset, and the logger column entirely",
)

# Widest first: `choose` takes the first that fits.
LAYOUTS = (FULL, COMPACT, MINIMAL)
BY_NAME = {layout.name: layout for layout in LAYOUTS}


def terminal_width(default: int = 80) -> int:
    """What `shutil` measures, or `default` where there is nothing to measure.

    A process writing into a pipe has no width of its own, which is one of the
    reasons the reader half of this module exists: it runs in the terminal the
    person is actually looking at.
    """
    return shutil.get_terminal_size(fallback=(default, 24)).columns


CONTEXTS = ("hidden", "shown")


def log_context(requested: str) -> bool:
    """Whether the console draws inherited keys.

    Here rather than beside the other settings because it steers this module
    and nothing else, and because both readers of it are here: the process
    drawing its own records, and `main` drawing somebody else's. When it lived
    next to the rest, `main` compared the string inline instead and
    `DJANGO_LOG_CONTEXT=show` quietly meant `hidden` in the one process where
    nothing else on screen would have hinted at it.
    """
    chosen = requested.strip().lower()
    if chosen not in CONTEXTS:
        raise ValueError(f"DJANGO_LOG_CONTEXT={requested!r} is not {' or '.join(CONTEXTS)}.")
    return chosen == "shown"


def choose(width: int, forced: str = "") -> Layout:
    """The widest layout that fits, unless one was named outright.

    A name Python does not recognise is refused rather than ignored, for the
    same reason a log level is: being quietly given something other than what
    you asked for is worse than being stopped.
    """
    if forced:
        wanted = forced.strip().lower()
        if wanted not in BY_NAME:
            names = ", ".join(BY_NAME)
            raise ValueError(f"DJANGO_LOG_LAYOUT={forced!r} is not a layout. Use one of: {names}.")
        return BY_NAME[wanted]
    return next(layout for layout in LAYOUTS if width >= layout.needs)


def announcement(layout: Layout, width: int, forced: str = "") -> str:
    """One line, once, saying which layout this process picked and why.

    Empty when the layout drops nothing, because there is then nothing a
    reader would be surprised by. Printed at startup and never per record, and
    only when drawing for a terminal -- a JSON stream has no layout, and a
    collector should not have to skip a line of prose.
    """
    if not layout.drops:
        return ""

    if forced:
        why = f"forced by DJANGO_LOG_LAYOUT={layout.name}"
    else:
        wider = LAYOUTS[LAYOUTS.index(layout) - 1]
        why = f"terminal {width} cols; {wider.name} needs {wider.needs}"

    return f"console layout: {layout.name} ({why})\ndropped: {layout.drops}.  Override: DJANGO_LOG_LAYOUT=full"


# The whole of an ISO-8601 instant with milliseconds and an offset. Every other
# timestamp is padded to it, because a column that changes width is not one.
ISO_WIDTH = len("2026-08-23T14:32:07.412-04:00")
TIME_WIDTH = len("14:32:07.412")

# The time of day, however much precision the writer chose, before whatever
# offset it appended. `timespec="seconds"` and a `Z` suffix are both ordinary
# in a stream from somewhere else, and this module is documented to accept one.
TIME = re.compile(r"^(\d{2}:\d{2}:\d{2}(?:\.\d+)?)")


def clock(timestamp: str, style: str) -> str:
    """The recorded ISO-8601 instant, shortened to a time where asked.

    The record always carries the whole thing. Which characters of it a layout
    draws is a drawing decision, so it is made here and not at write time --
    otherwise a narrow terminal would produce a narrower record, and the JSON a
    collector received would depend on who was watching.

    Parsed rather than sliced, and padded either way. A fixed slice assumes the
    precision this application happens to write, and `redraw` is for streams
    from anywhere: cutting `14:32:07+00:00` at twelve characters leaves a
    trailing colon, and a shorter time leaves the message column somewhere new
    on every line, which is the one thing the columns exist to prevent.
    """
    if style == "iso":
        return timestamp.ljust(ISO_WIDTH)
    _, _, rest = timestamp.partition("T")
    found = TIME.match(rest or timestamp)
    return (found.group(1) if found else timestamp).ljust(TIME_WIDTH)


def name(logger: str, width: int, style: str) -> str:
    """The logger, in `width` columns, truncated from the left.

    From the left because the tail is the informative part: told to choose
    between `inventory.sheet` and `sheet.batches`, a reader wants the second.
    """
    if style == "tail":
        logger = logger.rsplit(".", 1)[-1]
    if len(logger) > width:
        logger = "…" + logger[-(width - 1) :]
    return logger.ljust(width)


def depth_of(record: dict[str, Any]) -> int:
    """`depth`, when it is a number, and zero when it is anything else.

    It arrives from another process as often as from this one, and a renderer
    that raises on a field it did not like is a renderer that drops the record
    -- in-process, silently, through `logging.Handler.handleError`.
    """
    try:
        return int(record.get("depth") or 0)
    except (TypeError, ValueError):
        return 0


def nesting(depth: int) -> str:
    """Indentation for a call `depth` levels down, or a number past the cap.

    Past `MAX_DEPTH` the indentation stops growing and the depth is stated,
    because sixteen columns of leading space tells a reader less than the
    figure does and costs the message the room to be read.
    """
    if depth <= 0:
        return ""
    if depth > MAX_DEPTH:
        return f"{'  ' * MAX_DEPTH}[{depth}] "
    return "  " * depth


def safe(text: str) -> str:
    """Text to show, with everything a terminal would act on removed.

    `pairs` gets this for free from `!r`; the fields with columns of their own
    do not, and `event` is the one an attacker can choose.
    """
    return CONTROL.sub("", text)


def one_line(text: str) -> str:
    """`safe`, and on one line, for a field that has to sit in a column.

    A newline in the middle of a message puts the rest of it in column zero
    and every column after it out of alignment -- and a message with one in it
    is not exotic: Python hands `captureWarnings` a record ending in one.
    Whitespace is collapsed rather than cut so that nothing runs together.
    """
    return " ".join(safe(text).split())


def pairs(record: dict[str, Any]) -> str:
    """Everything specific to this record, as `key=value`, in a stable order.

    Sorted so that the same record drawn twice reads the same way, and so that
    two records differing in one field differ in one place on the screen.
    """
    return " ".join(f"{key}={record[key]!r}" for key in sorted(record))


def render(record: dict[str, Any], layout: Layout, colour: bool = False, context: bool = False) -> str:
    """One record, drawn.

    `context` puts the bound keys back: they are hidden by default because they
    are on every line, and shown when the reason you are reading at all is to
    follow one of them.
    """
    level = str(record.get("level", "info")).lower()
    columns = [clock(one_line(str(record.get("timestamp", ""))), layout.timestamp)]

    tag = level.upper().ljust(LEVEL_WIDTH)
    columns.append(f"{COLOURS.get(level, '')}{tag}{RESET}" if colour and level in COLOURS else tag)

    if layout.logger is not None:
        logger = name(one_line(str(record.get("logger", ""))), layout.logger, layout.logger_style)
        columns.append(f"{DIM}{logger}{RESET}" if colour else logger)

    body = nesting(depth_of(record)) + one_line(str(record.get("event", "")))

    # Which keys were inherited rather than stated, taken from the record where
    # the writer said so. A key the caller passed on this one call is never
    # hidden: the console and the JSON would then disagree about what was
    # logged, which is the one property the whole arrangement exists to hold.
    inherited = record.get(BOUND_KEYS)
    hidden: frozenset[str] | set[str] = BOUND if inherited is None else set(inherited)
    blocks = {key: record[key] for key in UNDER if isinstance(record.get(key), str) and record[key]}
    rest = {
        key: value
        for key, value in record.items()
        if key not in STRUCTURAL and key not in blocks and (context or key not in hidden)
    }
    if rest:
        drawn = pairs(rest)
        body += " " + (f"{DIM}{drawn}{RESET}" if colour else drawn)

    lines = [" ".join([*columns, body])]

    # `stack_info` AND NOT `stack`, which `redaction.ALLOWED_LOG_KEYS` says why
    # of and was corrected first. What is this file's own is the cost of having
    # had it wrong: a captured stack was drawn neither underneath nor as
    # `key=value` under its real name, and arrived as one escaped line in the
    # column beside the message. `UNDER` above is the rest of it.
    lines.extend(safe(text) for text in blocks.values())
    return "\n".join(lines)


def in_colour(stream: TextIO) -> bool:
    """Colour for a terminal, never for a pipe, and never against NO_COLOR.

    Piping to `grep`, `less` or a file has to give clean text: escape codes in
    a saved log are noise the next reader has to strip.
    """
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def parse(text: str) -> dict[str, Any] | None:
    """The record in a line, allowing for something in front of it.

    `docker compose logs` writes `backend-1  | ` before every line, and
    `kubectl logs --prefix` and `--timestamps` add their own. A reader that
    accepts only a pure record therefore renders nothing at all from the
    pipeline every document here prints, and says nothing about why -- it
    passes the line through, so what you get back is the JSON you were trying
    to escape. So the first `{` is found and the rest tried from there.

    The prefix is dropped rather than kept. It names a service the reader
    already chose, and putting it back would move the message column that the
    columns exist to hold still.
    """
    start = text.find("{")
    if start < 0:
        return None
    try:
        record = json.loads(text[start:])
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def redraw(lines: Iterable[str], layout: Layout, colour: bool = False, context: bool = False) -> Iterator[str]:
    """Render a JSON stream, passing anything that is not JSON straight through.

    Passing it through rather than dropping it, because a stream from a real
    container is not pure: an interpreter's dying traceback and whatever a
    dependency wrote to standard output before logging was configured both
    appear there, and those are exactly the lines somebody piping the stream
    through this is trying to read.
    """
    for line in lines:
        text = line.rstrip("\n")
        record = parse(text)
        yield render(record, layout, colour, context) if record is not None else text


def main() -> None:
    """Read JSON records on standard input and draw them for this terminal.

    Where the width is measured is the point of running this at all: the
    process that wrote the stream was writing into a pipe and could not know
    it, while this is attached to the terminal a person is looking at.
    """
    # Read through the same functions the server reads them through, so that
    # a value the server refuses cannot be silently reinterpreted here.
    forced = setting("DJANGO_LOG_LAYOUT")
    width = terminal_width()
    layout = choose(width, forced)
    context = log_context(setting("DJANGO_LOG_CONTEXT"))

    said = announcement(layout, width, forced)
    if said:
        print(said, file=sys.stderr)

    try:
        for line in redraw(sys.stdin, layout, in_colour(sys.stdout), context):
            print(line, flush=True)
    except BrokenPipeError:
        # `| head`, `| grep -m1` and quitting `less` all close the pipe, and
        # every documented use of this is a follow. A traceback dumped over
        # the stream somebody was reading is not a useful last word, and
        # Python flushes standard output at exit -- so it is pointed at
        # nothing first, or the flush raises the same thing again.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    main()
