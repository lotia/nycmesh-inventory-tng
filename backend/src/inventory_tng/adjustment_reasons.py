"""The reasons an adjustment may give (admin/counts.py): every write to the
adjustment_reason table, which administrators change under Site settings.
A current reason's name is unique, ignoring case (its index refuses a
second); a retired one is no longer offered, and adjustments that gave it
keep it. Each change but an add is to a row locked first (locked)."""

from datetime import datetime

import structlog
from sqlalchemy import exc, func, select
from sqlalchemy.orm import Session

from inventory_tng.db import Seen, get_locked, unchanged
from inventory_tng.models import AdjustmentReason

log = structlog.get_logger(__name__)

# The reason closing an open package gives (docs/decisions/0020): kept
# apart from Lost or stolen, so cable used is told from cable lost.
USED_UP = "Used up"


# The reason a count gives for an open package not there (0020).
LOST = "Lost or stolen"


def used_up(session: Session) -> AdjustmentReason:
    """The Used up reason, current, as migration 0026 seeds it; made again
    if an administrator renamed it, and brought back if retired."""
    return seeded(session, USED_UP)


def seeded(session: Session, name: str) -> AdjustmentReason:
    """A reason the app gives itself, current: made again if an
    administrator renamed it, and brought back if retired."""
    named = select(AdjustmentReason).where(
        func.lower(AdjustmentReason.name) == name.lower()
    )
    # A current one first: only a current name is unique.
    reason = session.scalars(
        named.order_by(AdjustmentReason.retired_at.is_not(None), AdjustmentReason.id)
    ).first()
    if reason is None:
        try:
            with session.begin_nested():
                reason = add(session, name)
        except exc.IntegrityError:
            # Made by a close at the same time, now committed.
            return session.scalars(
                named.where(AdjustmentReason.retired_at.is_(None))
            ).one()
        log.info("adjustment reason added", adjustment_reason_id=reason.id)
        return reason
    if reason.retired_at is not None:
        relocked = locked(session, reason.id)
        assert relocked is not None  # Read just now; reasons are never deleted.
        restore(relocked)
        log.info("adjustment reason restored", adjustment_reason_id=reason.id)
    return reason


def locked(session: Session, reason_id: int) -> AdjustmentReason | None:
    """The reason, locked and read as locked, to change; None if there is
    none."""
    return get_locked(session, AdjustmentReason, reason_id)


def add(session: Session, name: str) -> AdjustmentReason:
    reason = AdjustmentReason(name=name)
    session.add(reason)
    session.flush()
    return reason


def rename(reason: AdjustmentReason, name: str, *, seen: Seen) -> None:
    unchanged(reason, seen)
    reason.name = name


def retire(reason: AdjustmentReason, at: datetime) -> None:
    reason.retired_at = at


def restore(reason: AdjustmentReason) -> None:
    reason.retired_at = None
