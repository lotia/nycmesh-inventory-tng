"""The import's readings file (docs/decisions/0021, Ali, 2026-11-12,
inventory-tng-c0c.5): how each unit typed after a number in the names an
import brings is read, proposed in a file the operator corrects once and
keeps, then loaded into the reading_rule table, the first rules a new
database has. The sheet import (inventory-tng-8i4.17) calls `propose`
for its dry run and `load` before it writes a name, as does the command
line (catalogue/command.py).

`propose` reads names, one a line, and needs no database: it writes the
file, keeping each row, correction and respell the file already has, so a
later dry run updates it. `load` writes a rule for each unit the file reads, by no
administrator (`decided_by_admin_id` NULL), and leaves alone, saying so,
any rule an administrator has set otherwise. Proposals are the seeding
page's (reading_rules.propose); it proposes again what is left after an
import.

The file is UTF-8, tab-separated, one unit a row, sorted by unit, after
comment lines (#) saying what each column holds (HEADER)."""

import csv
import io
import sys
from collections.abc import Iterable, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import models
from inventory_tng.catalogue import items, reading_rules, units
from inventory_tng.core.db import lock_catalogue, refusal
from inventory_tng.models import ReadingRule

log = structlog.get_logger(__name__)

COLUMNS = (
    "unit",
    "proposed",
    "correction",
    "respell",
    "offered",
    "why",
    "names",
    "near",
    "examples",
)
# Between a row's examples and words near it.
JOIN = " | "
# What the respell column says for yes; empty is no.
YES = "yes"
# Why, for a row a later dry run no longer proposes: still loaded.
NOT_PROPOSED = "not proposed for these names; loaded all the same, or delete the row"
HEADER = f"""\
# How each unit typed after a number in the import's names is read.
# To read one otherwise, write a reading in "correction"; left empty,
# "proposed" is taken. A unit with neither stops the load until given one.
# A reading is one of "offered", by its symbol, the part before brackets;
# {reading_rules.NOT_A_UNIT} is not a unit. A later dry run keeps every row,
# its correction and respell; one it no longer proposes says so in "why".
#   unit: as typed after a number, case kept; one rule reads it in every name
#   respell: {YES} to save names with the reading's symbol (305M as 305m);
#     left empty, names are saved as typed
#   why: what the proposal is by; names: how many names need it settled
#   near: words near it in the names where they settle it, how often
#   examples: a few of the names that need it settled
"""


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
        "respell": "",
        "offered": JOIN.join(offered),
        "why": proposal.why or "nothing settles it: choose",
        "names": str(proposal.count),
        "near": JOIN.join(f"{word} {count}" for word, count in near),
        "examples": JOIN.join(proposal.names),
    }


def read(text: TextIO) -> list[dict[str, str]]:
    """The rows of a readings file, by COLUMNS, comment rows (#) and empty
    ones skipped, even quoted, as a spreadsheet may save them. ValueError if
    its columns are not COLUMNS."""
    rows = (
        row
        for row in csv.reader(text, dialect="excel-tab")
        if any(row) and not row[0].startswith("#")
    )
    if tuple(next(rows, ())) != COLUMNS:
        raise ValueError(f"its columns must be: {' '.join(COLUMNS)}")
    return [
        dict(zip(COLUMNS, [*row, *[""] * len(COLUMNS)], strict=False)) for row in rows
    ]


def write(text: TextIO, rows: Iterable[dict[str, str]]) -> None:
    text.write(HEADER)
    writer = csv.DictWriter(text, COLUMNS, dialect="excel-tab", lineterminator="\n")
    writer.writeheader()
    writer.writerows(sorted(rows, key=lambda row: row["unit"]))


@dataclass
class Proposed:
    """What a dry run wrote: rows, of which corrected, to choose (neither
    proposed nor corrected), and no longer proposed (NOT_PROPOSED)."""

    rows: int
    corrected: int
    to_choose: int
    not_proposed: int


def propose(names: Iterable[str], path: Path) -> Proposed:
    """The readings file at `path` written for `names`, as the seeding page
    proposes, by no rule (an import's database has none): every row it had
    kept with its correction and respell, one no longer proposed as it was
    but said so (NOT_PROPOSED), as loading it still reads names by it.
    ValueError if the file there is not a readings file, or has a unit twice."""
    kept = {}
    if path.exists():
        with open_text(path) as text:
            earlier = read(text)
        kept = {row["unit"]: row for row in earlier}
        if len(kept) < len(earlier):
            raise ValueError("a unit has more than one row; keep one")
    canonical = [models.canonical(name) for name in names]
    rows = {
        unit: {**row, "why": NOT_PROPOSED, "names": "0"} for unit, row in kept.items()
    }
    for proposal in reading_rules.propose(canonical, {}):
        row = _row(proposal)
        if (old := kept.get(row["unit"])) is not None:
            row["correction"], row["respell"] = old["correction"], old["respell"]
        rows[row["unit"]] = row
    with path.open("w", encoding="utf-8", newline="") as text:
        write(text, rows.values())
    done = Proposed(
        len(rows),
        sum(bool(row["correction"]) for row in rows.values()),
        sum(not (row["proposed"] or row["correction"]) for row in rows.values()),
        sum(row["why"] == NOT_PROPOSED for row in rows.values()),
    )
    log.info(
        "unit readings proposed",
        units=done.rows,
        corrected=done.corrected,
        to_choose=done.to_choose,
        not_proposed=done.not_proposed,
    )
    return done


def chosen(rows: Sequence[dict[str, str]]) -> tuple[dict[str, units.Rule], list[str]]:
    """The rule each row reads its unit by, its correction else its
    proposal; and what is wrong with any row, by its unit, in words."""
    rules: dict[str, units.Rule] = {}
    problems = []
    for row in rows:
        unit = units.rule_key(row["unit"].strip())
        reading = row["correction"].strip() or row["proposed"].strip()
        respell = row["respell"].strip().lower()
        if not unit:
            problems.append("a row has no unit")
        elif unit in rules:
            problems.append(f"{unit}: more than one row")
        elif not reading:
            problems.append(f"{unit}: no reading; write one in its correction")
        elif respell not in {"", YES}:
            problems.append(f"{unit}: respell is {YES} or empty, not {row['respell']}")
        elif reading == reading_rules.NOT_A_UNIT and respell:
            problems.append(f"{unit}: {reading} is never respelt; empty its respell")
        else:
            offers = reading_rules.offered(unit, units.Rule(None))
            rule = reading_rules.chosen_rule(reading, offers, respell=respell == YES)
            if rule is not None:
                rules[unit] = rule
            else:
                allowed = sorted({str(each) for each in offers})
                problems.append(
                    f"{unit}: {reading} is not one of its readings: "
                    + " ".join([*allowed, reading_rules.NOT_A_UNIT])
                )
    return rules, problems


@dataclass
class Loaded:
    """What load did: rules written (new or changed), those already so,
    and those an administrator set otherwise, left as they are with what
    the file says; and what each rule written over said before, by token
    (an earlier load's: an administrator's is never written over)."""

    written: list[ReadingRule]
    same: int
    kept: list[tuple[ReadingRule, units.Rule]]
    was: dict[str, units.Rule]


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
    kept: list[tuple[ReadingRule, units.Rule]] = []
    loading: dict[str, units.Rule] = {}
    for token, rule in rules.items():
        row = held.get(token)
        if row is not None and row.decided_by_admin_id is not None and row.rule != rule:
            kept.append((row, rule))
        else:
            loading[token] = rule
    was = {token: row.rule for token, row in held.items()}
    written, _ = refusal(
        session,
        lambda: reading_rules.decide(session, loading, None),
        items.READ_ALIKE,
    )
    if written is None:
        words = reading_rules.read_alike_words(
            session, loading, {**models.reading_rules(session), **loading}
        )
        log.info("unit readings refused", read_alike=len(words))
        return words
    log.info(
        "unit readings loaded",
        reading_rule_ids=[row.id for row in written],
        kept_reading_rule_ids=[row.id for row, _ in kept],
    )
    return Loaded(written, len(loading) - len(written), kept, was)


def open_text(path: Path) -> AbstractContextManager[TextIO]:
    """The file at `path` to read, or standard input, left open, for -: as
    UTF-8 whatever the locale, a byte order mark dropped."""
    if str(path) == "-":
        if isinstance(sys.stdin, io.TextIOWrapper):
            sys.stdin.reconfigure(encoding="utf-8-sig")
        return nullcontext(sys.stdin)
    return path.open(encoding="utf-8-sig", newline="")
