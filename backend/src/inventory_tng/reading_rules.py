"""Reading rules: how a number and its unit, as typed, are read, settled by
an administrator (models.ReadingRule, docs/decisions/0021, 2026-11-15):
every write to the reading_rule table. Each change reads every name with
that number again (items.reread), and is refused if two names would then
read alike (items.READ_ALIKE), holding the catalogue lock, as every name's
write does. The seeding page proposes rules from every name there is
(propose), by the words near each number and its unit (EVIDENCE)."""

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, union_all
from sqlalchemy.orm import Session

from inventory_tng import items, units
from inventory_tng.db import Seen, enforce, lock_catalogue, locked, relock
from inventory_tng.models import (
    CURRENT_ALIAS,
    Item,
    ItemAlias,
    ItemPackage,
    ReadingRule,
)

# Refused: a reading no unit has, or a rule respelling no unit.
NOT_A_READING = "reading_rule_reading"


def _check(rule: units.Rule) -> None:
    if rule.reading is None:
        enforce(NOT_A_READING, when=rule.respell)
    else:
        enforce(NOT_A_READING, when=units.reading_of(rule.reading) is None)


def decide(
    session: Session, chosen: Mapping[str, units.Rule], admin_id: int | None
) -> list[ReadingRule]:
    """Each token of `chosen` (units.rule_key) read as its rule says, by
    `admin_id` (None: the import), now: its rule written, or changed if it
    has one; then every name with it read again. The rules written or
    changed, those already so left as they are."""
    lock_catalogue(session)
    for rule in chosen.values():
        _check(rule)
    held = {
        row.token: row
        for row in session.scalars(
            locked(
                select(ReadingRule)
                .where(ReadingRule.token.in_(list(chosen)))
                .order_by(ReadingRule.id)
            )
        )
    }
    written = []
    for token, rule in chosen.items():
        row = held.get(token)
        if row is not None and row.rule == rule:
            continue
        if row is None:
            row = ReadingRule(token=token)
            session.add(row)
        _set(row, rule, admin_id)
        written.append(row)
    session.flush()
    items.reread(session, [row.token for row in written])
    return written


def _set(row: ReadingRule, rule: units.Rule, admin_id: int | None) -> None:
    row.reading, row.respell = rule.reading, rule.respell
    row.decided_by_admin_id, row.decided_at = admin_id, datetime.now(UTC)


def change(
    session: Session, row: ReadingRule, rule: units.Rule, admin_id: int, *, seen: Seen
) -> None:
    """A rule changed by an administrator, unless changed since `seen`;
    then every name with its token read again."""
    relock(session, row, seen=seen)
    _check(rule)
    _set(row, rule, admin_id)
    session.flush()
    items.reread(session, [row.token])


def delete(session: Session, row: ReadingRule) -> None:
    """A rule deleted: its token read by the unit table again, every name
    with it read so."""
    relock(session, row)
    session.delete(row)
    session.flush()
    items.reread(session, [row.token])


# Words that, near a number and its unit in a name, say what kind it is
# (Ali, 2026-11-15): "10G" near "SFP+" or "uplink" is a data rate, "305M"
# near "spool" a length. Compared in lower case.
EVIDENCE: dict[str, frozenset[str]] = {
    "data rate": frozenset(
        (
            "sfp",
            "sfp+",
            "qsfp",
            "switch",
            "uplink",
            "port",
            "ports",
            "ethernet",
            "router",
            "transceiver",
            "nic",
            "lan",
            "wan",
            "link",
            "gbe",
            "fiber",
            "fibre",
            "backhaul",
        )
    ),
    "length": frozenset(
        (
            "spool",
            "cable",
            "cord",
            "wire",
            "reel",
            "roll",
            "drop",
            "patch",
            "conduit",
            "jumper",
            "pigtail",
            "rope",
        )
    ),
    "gain": frozenset(("antenna", "dish", "sector", "omni", "horn")),
    "voltage": frozenset(("battery", "psu", "supply", "adapter", "injector")),
    "mass": frozenset(("weight", "weighs")),
}
_WORDS = re.compile(r"[A-Za-z][A-Za-z+]*")


@dataclass
class Proposal:
    """A number and its unit as typed that no rule settles, in the names
    there are, and how the seeding page proposes to read it: `found`, as
    first found; how many names have it, a few of them, and the words
    near it that say a kind (`near`, each with how many names have it);
    and the reading proposed, if anything settles one, with why. Unsettled,
    an administrator chooses."""

    found: units.Found
    count: int = 0
    names: list[str] = field(default_factory=list)
    near: Counter[str] = field(default_factory=Counter)
    proposed: units.Reading | None = None
    why: str = ""


# Names shown with each proposal, at most.
EXAMPLES = 3


def propose(names: Iterable[str], rules: units.Rules) -> list[Proposal]:
    """What the seeding page proposes, by token as typed (units.rule_key):
    each number and unit in `names` no rule settles that the form would ask
    about, or that the table reads only in another case or by the profile
    (units.readings); proposed by the kind the words near it say, else by
    the table's one likely reading. Most names first."""
    found: dict[str, Proposal] = {}
    for name in names:
        words = {word.lower() for word in _WORDS.findall(name)}
        for each in units.found_in(name, rules):
            if each.rule is not None or not (each.asks or _loose(each)):
                continue
            proposal = found.setdefault(each.key, Proposal(each))
            proposal.count += 1
            if len(proposal.names) < EXAMPLES:
                proposal.names.append(name)
            kinds = {reading.unit.kind for reading in each.offered}
            proposal.near.update(
                word
                for word in words
                for kind in kinds
                if word in EVIDENCE.get(kind, ())
            )
    for proposal in found.values():
        _settle(proposal)
    return sorted(found.values(), key=lambda p: (-p.count, p.found.key))


def _loose(found: units.Found) -> bool:
    """Whether the table reads it, but not as spelt."""
    return found.read is not None and (found.read, True) not in units.readings(
        found.token
    )


def _settle(proposal: Proposal) -> None:
    """The reading the words near it say, the profile's first among those
    as near; else the one the table reads it as."""
    scores = {
        reading: sum(
            count
            for word, count in proposal.near.items()
            if word in EVIDENCE.get(reading.unit.kind, ())
        )
        for reading in proposal.found.offered
    }
    best = max(scores.values(), default=0)
    if best > 0:
        proposal.proposed = next(r for r, score in scores.items() if score == best)
        words = sorted(
            word
            for word in proposal.near
            if word in EVIDENCE.get(proposal.proposed.unit.kind, ())
        )
        proposal.why = "near " + ", ".join(words)
    elif proposal.found.read is not None:
        proposal.proposed = proposal.found.read
        proposal.why = "the one likely reading"


def names(session: Session) -> list[str]:
    """Every name saved: items', packages' and current aliases'."""
    return list(
        session.scalars(
            union_all(
                select(Item.name),
                select(ItemPackage.name),
                select(ItemAlias.name).where(CURRENT_ALIAS),
            )
        )
    )
