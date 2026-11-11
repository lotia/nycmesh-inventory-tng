"""The reasons an adjustment may give (admin/counts.py): every write to the
adjustment_reason table, which administrators change under Site settings.
A current reason's name is unique, ignoring case (its index refuses a
second); a retired one is no longer offered, and adjustments that gave it
keep it. Each change but an add is to a row locked first (locked)."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng.db import Seen, get_locked, unchanged
from inventory_tng.models import AdjustmentReason

# The reason closing an open package gives (docs/decisions/0020): kept
# apart from Lost or stolen, so cable used is told from cable lost.
USED_UP = "Used up"


def used_up(session: Session) -> AdjustmentReason:
    """The Used up reason, current, as migration 0024 seeds it; made again
    if an administrator renamed it, and brought back if retired."""
    reason = session.scalars(
        select(AdjustmentReason).where(
            func.lower(AdjustmentReason.name) == USED_UP.lower()
        )
    ).first()
    if reason is None:
        return add(session, USED_UP)
    if reason.retired_at is not None:
        relocked = locked(session, reason.id)
        assert relocked is not None  # Read just now; reasons are never deleted.
        restore(relocked)
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
