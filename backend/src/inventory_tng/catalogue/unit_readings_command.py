"""The import's readings file from the command line (catalogue/unit_readings.py;
DEPLOYMENT.md, Unit readings):

    uv run python -m inventory_tng.manage unit-readings \\
        propose NAMES --file readings.tsv
    kubectl exec -i deploy/inventory -- \\
        python -m inventory_tng.manage unit-readings load - < readings.tsv

`propose` needs no database; both read "-" as standard input."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import structlog
from sqlalchemy.orm import Session

from inventory_tng.catalogue import reading_rules, units
from inventory_tng.catalogue.unit_readings import (
    chosen,
    load,
    open_text,
    propose,
    read,
)
from inventory_tng.core.db_sessions import CLI_ACTOR, set_actor
from inventory_tng.core.operator_commands import run_command, set_up

log = structlog.get_logger(__name__)


def _said(rule: units.Rule) -> str:
    said = rule.reading or reading_rules.NOT_A_UNIT
    return f"{said}, respelt" if rule.respell else said


def parse(
    argv: Sequence[str] | None = None, prog: str | None = None
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=prog,
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


def main(argv: Sequence[str] | None = None, prog: str | None = None) -> int:
    args = parse(argv, prog)
    if args.command == "propose":
        if set_up() is None:
            return 1
        return run_propose(args)
    return run_command(run_load, args)


def run_propose(args: argparse.Namespace) -> int:
    try:
        with open_text(args.names) as text:
            names = [name for line in text if (name := line.strip())]
    except ValueError as error:
        print(f"{args.names}: {error}", file=sys.stderr)
        return 1
    try:
        done = propose(names, args.file)
    except ValueError as error:
        print(f"{args.file}: {error}", file=sys.stderr)
        return 1
    print(
        f"Wrote {args.file}: {done.rows} units, {done.corrected} corrected, "
        f"{done.to_choose} to choose, {done.not_proposed} no longer proposed."
    )
    return 0


def run_load(session: Session, args: argparse.Namespace) -> int:
    try:
        with open_text(args.file) as text:
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
        before = done.was.get(row.token)
        was = f" (was {_said(before)}, by an earlier load)" if before else ""
        print(f"{row.token}: {_said(row.rule)}{was}")
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
