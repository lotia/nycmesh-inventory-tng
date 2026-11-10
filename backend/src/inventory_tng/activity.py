"""What every administrator is told of: changes to who may sign in, such as
an administrator reset or retired (docs/decisions/0012). Each is recorded
here once, as it happens, mailed to every administrator with an address,
and shown on the administrators page ("Recent changes") and on the home
page ("Since you last signed in"), so those without an address hear of it
too. Kept for good, as the ledger is.

A new kind of activity is a name in models.ActivityKind, its words in WORDS
and its migration's check: nothing else here changes."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

import structlog
from opentelemetry import metrics
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import mail
from inventory_tng.mail import Notifier
from inventory_tng.models import Activity, ActivityKind, Admin

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

# How each kind reads, from who did it and the administrator it was done to
# ("" for a kind done to none).
WORDS: Mapping[ActivityKind, Callable[[str, str], str]] = {
    "invited": lambda by, admin: f"{by} invited {admin}",
    "sign_in_reset": lambda by, admin: f"{by} reset {admin}'s sign-in",
    "retired": lambda by, admin: f"{by} retired {admin}",
    "restored": lambda by, admin: f"{by} brought back {admin}",
    "recovery_link_made": lambda by, admin: (
        f"{by} made a link for {admin} with the recovery token"
    ),
    "sign_in_settings_changed": lambda by, _: f"{by} changed the sign-in settings",
    "locked_down": lambda by, _: (
        f"{by} retired every administrator and revoked every registration code"
    ),
    "let_in": lambda by, admin: f"{by} let {admin} back in",
    "authenticators_retired": lambda by, _: (
        f"{by} retired the authenticators a lost or leaked key sealed"
    ),
}
# Who did it, when no administrator did: a command run by the operator.
OPERATOR = "The operator"
# How many a page lists, newest first.
SHOWN = 20

recorded = meter.create_counter(
    "inventory.admin.activity",
    unit="{change}",
    description="Changes every administrator is told of, by kind",
)


@dataclass(frozen=True)
class Shown:
    at: datetime
    words: str


def record(
    session: Session,
    kind: ActivityKind,
    by_admin_id: int | None,
    admin_id: int | None,
    notify: Notifier | None,
) -> Activity:
    """Keep it, and mail every current administrator with an address but
    the one it was done to, whom the caller tells in their own words. Mail
    goes after the reply, so only once the caller has committed; without
    `notify`, as from a command or where the caller mails its own words to
    everyone, nobody is mailed here."""
    row = Activity(kind=kind, by_admin_id=by_admin_id, admin_id=admin_id)
    session.add(row)
    session.flush()
    recorded.add(1, {"kind": kind})
    log.info(
        "activity recorded",
        activity_id=row.id,
        kind=kind,
        by_admin_id=by_admin_id,
        target_admin_id=admin_id,
    )
    if notify is not None and notify.on:
        notify.tell_admins(session, mail.activity(words(session, row)), but=admin_id)
    return row


def words(session: Session, row: Activity) -> str:
    """How one reads."""
    return _words(row, _usernames(session, (row.by_admin_id, row.admin_id)))


def recent(
    session: Session,
    *,
    since: datetime | None = None,
    not_by: int | None = None,
) -> list[Shown]:
    """The newest, at most SHOWN: all of them, or only those from `since` on
    and not done by `not_by`. From, not after: both are the time their
    transaction began, which one may share."""
    query = select(Activity).order_by(Activity.id.desc()).limit(SHOWN)
    if since is not None:
        query = query.where(Activity.at >= since)
    if not_by is not None:
        query = query.where(Activity.by_admin_id.is_distinct_from(not_by))
    rows = list(session.scalars(query))
    names = _usernames(
        session, (i for row in rows for i in (row.by_admin_id, row.admin_id))
    )
    return [Shown(row.at, _words(row, names)) for row in rows]


def _usernames(session: Session, ids: Iterable[int | None]) -> dict[int, str]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    found = session.execute(
        select(Admin.id, Admin.username).where(Admin.id.in_(wanted))
    )
    return {admin_id: username for admin_id, username in found}


def _words(row: Activity, names: Mapping[int, str]) -> str:
    by = OPERATOR if row.by_admin_id is None else names[row.by_admin_id]
    admin = names[row.admin_id] if row.admin_id is not None else ""
    return WORDS[row.kind](by, admin)
