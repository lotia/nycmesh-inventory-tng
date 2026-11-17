"""What the administrators' pages read (admin/admins.py): plain functions
taking a Session, each named for what it returns (docs/decisions/0023)."""

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng.models import Admin


def admins_by_username(session: Session) -> Sequence[Admin]:
    """Every administrator, retired too, by username ignoring case."""
    return session.scalars(select(Admin).order_by(func.lower(Admin.username))).all()
