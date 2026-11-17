"""What the settings page reads (admin/settings.py): plain functions taking
a Session, each named for what it returns (docs/decisions/0023)."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.models import AdjustmentReason


def reasons_current_first(session: Session) -> Sequence[AdjustmentReason]:
    """Every reason for an adjustment, the current ones first, then in the
    order they were made."""
    return session.scalars(
        select(AdjustmentReason).order_by(
            AdjustmentReason.retired_at.is_not(None), AdjustmentReason.id
        )
    ).all()
