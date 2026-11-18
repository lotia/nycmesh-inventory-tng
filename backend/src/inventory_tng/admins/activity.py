"""What every administrator is told of: changes to who may sign in, such as
an administrator reset or retired (docs/decisions/0012). Each is recorded
here once, as it happens, mailed to every administrator with an address,
and shown on the administrators page ("Recent changes") and on the home
page ("Since you last signed in"), so those without an address hear of it
too. Kept for good, as the ledger is.

A new kind of activity is a name in models.admins.ActivityKind, its words in
WORDS and its migration's check: nothing else here changes."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

import structlog
from opentelemetry import metrics
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.admins import mail_messages
from inventory_tng.core.mail import Notifier, Told
from inventory_tng.core.phrases import gettext, lazy_gettext
from inventory_tng.models.admins import Activity, ActivityKind, Admin

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

# How each kind reads, from who did it and the administrator it was done to
# ("" for a kind done to none).
WORDS: Mapping[ActivityKind, Callable[[str, str], str]] = {
    "invited": lambda by, admin: gettext(
        "%(by)s invited %(admin)s", by=by, admin=admin
    ),
    "sign_in_reset": lambda by, admin: gettext(
        "%(by)s reset %(admin)s's sign-in", by=by, admin=admin
    ),
    "retired": lambda by, admin: gettext(
        "%(by)s retired %(admin)s", by=by, admin=admin
    ),
    "restored": lambda by, admin: gettext(
        "%(by)s brought back %(admin)s", by=by, admin=admin
    ),
    "recovery_link_made": lambda by, admin: gettext(
        "%(by)s made a link for %(admin)s with the recovery token",
        by=by,
        admin=admin,
    ),
    "sign_in_settings_changed": lambda by, _: gettext(
        "%(by)s changed the sign-in settings", by=by
    ),
    "locked_down": lambda by, _: gettext(
        "%(by)s retired every administrator and revoked every registration code",
        by=by,
    ),
    "let_in": lambda by, admin: gettext(
        "%(by)s let %(admin)s back in", by=by, admin=admin
    ),
    "authenticators_retired": lambda by, _: gettext(
        "%(by)s retired the authenticators a lost or leaked key sealed", by=by
    ),
}
# Who did it, when no administrator did: a command run by the operator.
# Translators: who did something, at the start of a sentence.
OPERATOR = lazy_gettext("The operator")
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
    notify: Notifier,
    *,
    them: Told | None = None,
    others: Told | None = None,
    quiet: bool = False,
) -> Activity:
    """Keep it, and send the mail it calls for: `them` to the administrator
    it was done to, if given; to every other current administrator with an
    address, `others`, or the change in the feed's words, unless `quiet`,
    where the caller mails each one affected in their own words, as
    accounts.retire_authenticators does. Account changes are mailed from
    here, so none forgets one of the two. Mail goes after the reply, or
    after a command commits, so only if it does."""
    if them is not None and admin_id is None:
        raise ValueError("a mail for the one it was done to needs them")
    target = session.get(Admin, admin_id) if admin_id is not None else None
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
    if not notify.on:
        return row
    if them is not None and target is not None:
        notify.tell(target.email, target.id, them)
    if not quiet:
        told = others or mail_messages.activity(words(session, row))
        notify.tell_admins(session, told, but=admin_id)
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
    by = str(OPERATOR) if row.by_admin_id is None else names[row.by_admin_id]
    admin = names[row.admin_id] if row.admin_id is not None else ""
    return WORDS[row.kind](by, admin)
