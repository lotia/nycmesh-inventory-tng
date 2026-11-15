"""The import's readings file (docs/decisions/0021, Ali's Q5-A,
inventory-tng-c0c.5): how each unit typed after a number in the names an
import brings is read, proposed in a file the operator corrects once and
keeps, then loaded into the reading_rule table, the first rules a new
database has. The sheet import (inventory-tng-8i4.17) calls `propose`
for its dry run and `load` before it writes a name; from the command line:

    uv run python -m inventory_tng.unit_readings propose NAMES --file readings.tsv
    kubectl exec -i deploy/inventory -- \\
        python -m inventory_tng.unit_readings load - < readings.tsv

`propose` reads names, one a line, and needs no database: it writes the
file, keeping each correction the file already has, so a later dry run
updates it. `load` writes a rule for each unit the file reads, by no
administrator (`decided_by_admin_id` NULL), and leaves alone, saying so,
any rule an administrator has set otherwise. Proposals are the seeding
page's (reading_rules.propose); it proposes again what is left after an
import.

The file is UTF-8, tab-separated, one unit a row, sorted by unit, after
comment lines (#) saying what each column holds (HEADER)."""

import argparse
import csv
import sys
from collections.abc import Iterable, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import items, models, reading_rules, units
from inventory_tng.command import run_command, set_up
from inventory_tng.db import CLI_ACTOR, lock_catalogue, refusal, set_actor
from inventory_tng.models import ReadingRule

log = structlog.get_logger(__name__)

COLUMNS = (
    "unit",
    "proposed",
    "correction",
    "offered",
    "why",
    "names",
    "near",
    "examples",
)
HEADER = f"""\
# How each unit typed after a number in the import's names is read.
# To read one otherwise, write a reading in "correction"; left empty,
# "proposed" is taken. A unit with neither stops the load until given one.
# A reading is one of "offered", by its symbol, the part before brackets;
# {reading_rules.NOT_A_UNIT} is not a unit. A later dry run keeps corrections.
#   unit: as typed after a number, case kept; one rule reads it in every name
#   why: what the proposal is by; names: how many names need it settled
#   near: words near it in the names where they settle it, how often
#   examples: a few of the names that need it settled
"""
# Between a row's examples and words near it.
JOIN = " | "


def _called(reading: units.Reading) -> str:
    return f"{reading} ({reading.called})"


def _row(proposal: reading_rules.Proposal) -> dict[str, str]:
    found = proposal.found
    offered = [*map(_called, found.offered), f"{reading_rules.NOT_A_UNIT} (not a unit)"]
    near = sorted(proposal.near.items(), key=lambda pair: (-pair[1], pair[0]))
    return {
        "unit": found.key,
        "proposed": str(proposal.proposed) if proposal.proposed else "",
        "correction": "",
        "offered": JOIN.join(offered),
        "why": proposal.why or "nothing settles it: choose",
        "names": str(proposal.count),
        "near": JOIN.join(f"{word} {count}" for word, count in near),
        "examples": JOIN.join(proposal.names),
    }


def read(text: TextIO) -> list[dict[str, str]]:
    """The rows of a readings file, by COLUMNS, comment lines skipped.
    ValueError if its columns are not COLUMNS."""
    lines = (line for line in text if not line.startswith("#"))
    reader = csv.DictReader(lines, dialect="excel-tab")
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise ValueError(f"its columns must be: {' '.join(COLUMNS)}")
    return [{key: row.get(key) or "" for key in COLUMNS} for row in reader]


def write(text: TextIO, rows: Iterable[dict[str, str]]) -> None:
    text.write(HEADER)
    writer = csv.DictWriter(text, COLUMNS, dialect="excel-tab", lineterminator="\n")
    writer.writeheader()
    writer.writerows(sorted(rows, key=lambda row: row["unit"]))


@dataclass
class Proposed:
    """What a dry run wrote: rows, of which corrected, and to choose
    (neither proposed nor corrected)."""

    rows: int
    corrected: int
    to_choose: int


def propose(names: Iterable[str], path: Path) -> Proposed:
    """The readings file at `path` written for `names`, as the seeding page
    proposes, by no rule (an import's database has none): every row it had
    kept, its correction with it, a unit no longer found as it was."""
    kept = {}
    if path.exists():
        with path.open(encoding="utf-8", newline="") as text:
            kept = {row["unit"]: row for row in read(text)}
    canonical = [models.canonical(name) for name in names]
    rows = {**kept}
    for proposal in reading_rules.propose(canonical, {}):
        row = _row(proposal)
        if (earlier := kept.get(row["unit"])) is not None:
            row["correction"] = earlier["correction"]
        rows[row["unit"]] = row
    with path.open("w", encoding="utf-8", newline="") as text:
        write(text, rows.values())
    done = Proposed(
        len(rows),
        sum(bool(row["correction"]) for row in rows.values()),
        sum(not (row["proposed"] or row["correction"]) for row in rows.values()),
    )
    log.info(
        "unit readings proposed",
        units=done.rows,
        corrected=done.corrected,
        to_choose=done.to_choose,
    )
    return done


def chosen(rows: Sequence[dict[str, str]]) -> tuple[dict[str, units.Rule], list[str]]:
    """The rule each row reads its unit by, its correction else its
    proposal; and what is wrong with any row, by its unit, in words."""
    rules: dict[str, units.Rule] = {}
    problems = []
    for row in rows:
        unit = units.rule_key(row["unit"].strip())
        reading = (row["correction"] or row["proposed"]).strip()
        allowed = {str(r) for r in reading_rules.offered(unit, units.Rule(None))}
        if not unit:
            problems.append("a row has no unit")
        elif unit in rules:
            problems.append(f"{unit}: more than one row")
        elif not reading:
            problems.append(f"{unit}: no reading; write one in its correction")
        elif reading == reading_rules.NOT_A_UNIT:
            rules[unit] = units.Rule(None)
        elif reading in allowed:
            rules[unit] = units.Rule(reading)
        else:
            problems.append(
                f"{unit}: {reading} is not one of its readings: "
                + " ".join([*sorted(allowed), reading_rules.NOT_A_UNIT])
            )
    return rules, problems


@dataclass
class Loaded:
    """What load did: rules written (new or changed), those already so,
    and those an administrator set otherwise, left as they are with what
    the file says."""

    written: list[ReadingRule]
    same: int
    kept: list[tuple[ReadingRule, units.Rule]]


def load(session: Session, rules: dict[str, units.Rule]) -> Loaded | list[str]:
    """`rules` written by no administrator (the import), but each an
    administrator has set otherwise, left as it is: what was done; or,
    nothing written, the names that would read alike, by id
    (items.READ_ALIKE)."""
    # Held from here, so no administrator's rule is set before decide.
    lock_catalogue(session)
    held = {
        row.token: row
        for row in session.scalars(
            select(ReadingRule).where(ReadingRule.token.in_(list(rules)))
        )
    }
    kept = [
        (row, rule)
        for token, rule in rules.items()
        if (row := held.get(token)) is not None
        and row.decided_by_admin_id is not None
        and row.rule != rule
    ]
    left = {row.token for row, _ in kept}
    loading = {token: rule for token, rule in rules.items() if token not in left}
    written, broken = refusal(
        session,
        lambda: reading_rules.decide(session, loading, None),
        items.READ_ALIKE,
    )
    if broken is not None or written is None:
        groups = items.read_alike(
            session, loading, {**models.reading_rules(session), **loading}
        )
        log.info("unit readings refused", read_alike=len(groups))
        return reading_rules.read_alike_words(groups)
    log.info(
        "unit readings loaded",
        reading_rule_ids=[row.id for row in written],
        kept_reading_rule_ids=[row.id for row, _ in kept],
    )
    return Loaded(written, len(loading) - len(written), kept)


def _said(rule: units.Rule) -> str:
    return rule.reading or reading_rules.NOT_A_UNIT


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m inventory_tng.unit_readings",
        description="The import's readings file: how each unit typed after a "
        "number in its names is read.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    proposing = commands.add_parser(
        "propose",
        help="write the readings file for names (a dry run; no database)",
    )
    proposing.add_argument(
        "names", type=Path, help="a UTF-8 text file of names, one a line, or -"
    )
    proposing.add_argument(
        "--file",
        type=Path,
        required=True,
        help="the readings file, written; corrections it has are kept",
    )
    loading = commands.add_parser(
        "load", help="write a rule for each unit the readings file reads"
    )
    loading.add_argument("file", type=Path, help="the readings file, or -")
    return parser.parse_args(argv)


def _text(path: Path) -> AbstractContextManager[TextIO]:
    """The file at `path` to read, or standard input, left open, for -."""
    if str(path) == "-":
        return nullcontext(sys.stdin)
    return path.open(encoding="utf-8", newline="")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse(argv)
    if args.command == "propose":
        if set_up() is None:
            return 1
        return run_propose(args)
    return run_command(run_load, args)


def run_propose(args: argparse.Namespace) -> int:
    with _text(args.names) as text:
        names = [line.strip() for line in text if line.strip()]
    try:
        done = propose(names, args.file)
    except ValueError as error:
        print(f"{args.file}: {error}", file=sys.stderr)
        return 1
    print(
        f"Wrote {args.file}: {done.rows} units, {done.corrected} corrected, "
        f"{done.to_choose} to choose."
    )
    return 0


def run_load(session: Session, args: argparse.Namespace) -> int:
    try:
        with _text(args.file) as text:
            rows = read(text)
    except ValueError as error:
        print(f"{args.file}: {error}", file=sys.stderr)
        return 1
    rules, problems = chosen(rows)
    if problems:
        log.info("unit readings refused", problems=len(problems))
        print(f"{args.file}: nothing loaded.", *problems, sep="\n", file=sys.stderr)
        return 1
    set_actor(session, CLI_ACTOR)
    done = load(session, rules)
    if isinstance(done, list):
        print(
            "Nothing loaded: these names would read alike; rename one of each first.",
            *done,
            sep="\n",
            file=sys.stderr,
        )
        return 1
    for row in done.written:
        print(f"{row.token}: {_said(row.rule)}")
    for row, rule in done.kept:
        print(
            f"{row.token}: kept {_said(row.rule)}, set by an administrator; "
            f"the file says {_said(rule)}"
        )
    print(
        f"Loaded {len(done.written)} rules; {done.same} already so; "
        f"{len(done.kept)} kept as an administrator set them."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
