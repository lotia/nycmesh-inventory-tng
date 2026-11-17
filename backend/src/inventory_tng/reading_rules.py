"""Reading rules: how a unit, as typed after a number, is read, settled by
an administrator (models.ReadingRule, docs/decisions/0021, 2026-11-15):
every write to the reading_rule table. Each change reads every name with
that unit after a number again (items.reread), and is refused if two names
would then read alike (items.READ_ALIKE), holding the catalogue lock, as
every name's write does. The seeding page proposes rules from every name
there is (propose), by the words near each unit (units.CLUES), as the
import's readings file does from the names it brings (unit_readings)."""

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import reduce

from sqlalchemy import select, union_all
from sqlalchemy.orm import Session

from inventory_tng import items, units
from inventory_tng.core.db import Seen, enforce, lock_catalogue, locked, relock
from inventory_tng.core.phrases import gettext, lazy_gettext
from inventory_tng.models import (
    CURRENT_ALIAS,
    Item,
    ItemAlias,
    ItemPackage,
    ReadingRule,
)

# Refused: a reading no unit has, or a rule respelling no unit.
NOT_A_READING = "reading_rule_reading"
# What a form, or the import's readings file, says for "not a unit".
NOT_A_UNIT = "none"
# What each table's names are called, naming names that would read alike.
NAMED: dict[str, Callable[[int, str], str]] = {
    "item": lambda ident, name: gettext("item %(id)d (%(name)s)", id=ident, name=name),
    "item_package": lambda ident, name: gettext(
        "package %(id)d (%(name)s)", id=ident, name=name
    ),
    "item_alias": lambda ident, name: gettext(
        "alias %(id)d (%(name)s)", id=ident, name=name
    ),
}


def both(first: str, second: str) -> str:
    """Two names that would read alike, or more, the first already joined."""
    return gettext("%(first)s and %(second)s", first=first, second=second)


def near(words: str) -> str:
    """Why a reading is proposed: the words near it that settle it."""
    return gettext("elsewhere near %(words)s", words=words)


LIKELY = lazy_gettext("the one likely reading")


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


def delete(session: Session, row: ReadingRule, *, seen: Seen) -> None:
    """A rule deleted, unless changed since `seen`: its token read by the
    unit table again, every name with it read so."""
    relock(session, row, seen=seen)
    session.delete(row)
    session.flush()
    items.reread(session, [row.token])


@dataclass
class Proposal:
    """A unit as typed after a number (units.rule_key) that no rule settles
    and some name needs settled, and how the seeding page proposes to read
    it: `found`, as first found; how many names need it, a few of them; the
    kinds the words near it say in the names where they settle it
    (`near`, each clue word with how many names have it); and the reading
    proposed, if anything settles one, with why. Unsettled, an
    administrator chooses."""

    found: units.Found
    count: int = 0
    names: list[str] = field(default_factory=list)
    near: Counter[str] = field(default_factory=Counter)
    proposed: units.Reading | None = None
    why: str = ""


# Names shown with each proposal, at most.
EXAMPLES = 3


def propose(names: Iterable[str], rules: units.Rules) -> list[Proposal]:
    """What the seeding page proposes, by unit as typed (units.rule_key):
    each in `names` no rule settles, where the form would ask, or the table
    reads it only in another case or by the profile (units.readings), but
    not where the words near it settle it (units.CLUES), as those names
    need no rule; proposed by the kind the words near it say in those
    names, else by the profile's reading. Most names first."""
    found: dict[str, Proposal] = {}
    clued: dict[str, Counter[str]] = {}
    for name in names:
        for each in units.found_in(name, rules):
            if each.clue:
                clued.setdefault(each.key, Counter()).update(each.clue)
                continue
            if each.rule is not None or not (each.asks or _loose(each)):
                continue
            proposal = found.setdefault(each.key, Proposal(each))
            proposal.count += 1
            if len(proposal.names) < EXAMPLES:
                proposal.names.append(name)
    for key, proposal in found.items():
        proposal.near = clued.get(key, Counter())
        _settle(proposal)
    return sorted(found.values(), key=lambda p: (-p.count, p.found.key))


def _loose(found: units.Found) -> bool:
    """Whether the table reads it, but not as spelt."""
    return found.read is not None and (found.read, True) not in units.readings(
        found.token
    )


def _clues(reading: units.Reading) -> frozenset[str]:
    return units.CLUES.get(reading.unit.kind, frozenset())


def _settle(proposal: Proposal) -> None:
    """The reading the words near it say most often where they settle it,
    the profile's first among those as often; else the one it is read as."""
    scores = {
        reading: sum(
            count for word, count in proposal.near.items() if word in _clues(reading)
        )
        for reading in proposal.found.offered
    }
    best = max(scores.values(), default=0)
    if best > 0:
        proposal.proposed = next(r for r, score in scores.items() if score == best)
        words = sorted(w for w in proposal.near if w in _clues(proposal.proposed))
        proposal.why = near(", ".join(words))
    elif proposal.found.read is not None:
        proposal.proposed = proposal.found.read
        proposal.why = str(LIKELY)


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


def offered(token: str, rule: units.Rule) -> tuple[units.Reading, ...]:
    """The readings a rule's unit may be given: every one it spells
    (units.readings), whatever the number, and the rule's."""
    every = [reading for reading, _ in units.readings(token)]
    ruled = units.reading_of(rule.reading) if rule.reading else None
    if ruled is not None and ruled not in every:
        every.append(ruled)
    return tuple(every)


def chosen_rule(
    value: str, offered: Sequence[units.Reading], *, respell: bool = False
) -> units.Rule | None:
    """The rule a choice of reading (a form's, or the import's readings
    file's) says, if it is one offered: a reading by str(Reading), respelt
    in names if `respell`, or NOT_A_UNIT."""
    if value == NOT_A_UNIT:
        return units.Rule(None)
    if value in {str(reading) for reading in offered}:
        return units.Rule(value, respell)
    return None


def read_alike_words(
    session: Session, tokens: Iterable[str], rules: units.Rules
) -> list[str]:
    """Each group of names that would read alike by `rules`, of those with
    these units after a number (items.read_alike), in words, by id, each
    named as typed, not as shown (Ali, 2026-11-15, inventory-tng-dfz)."""
    return [
        reduce(both, (NAMED[each.table](each.id, each.name) for each in group))
        for group in items.read_alike(session, tokens, rules)
    ]
