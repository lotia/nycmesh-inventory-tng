"""Reading rules: how a number and its unit, as typed, are read, settled by
an administrator (models.ReadingRule, docs/decisions/0021, 2026-11-15):
every write to the reading_rule table. Each change reads every name with
that number again (items.reread), and is refused if two names would then
read alike (items.READ_ALIKE), holding the catalogue lock, as every name's
write does."""

from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import items, units
from inventory_tng.db import Seen, enforce, lock_catalogue, locked, relock
from inventory_tng.models import ReadingRule

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
