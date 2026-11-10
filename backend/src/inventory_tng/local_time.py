"""Showing times in the site's time zone (docs/decisions/0013).

Every time is stored, compared and sent in UTC. Only what people read is
turned into local time, here, with zoneinfo's rules for the zone, which know
when daylight saving starts and ends. The tzdata package ships those rules,
so they do not depend on the operating system's.
"""

import zoneinfo
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, literal, select
from sqlalchemy.orm import Session

from inventory_tng.models import Location, SiteSettings

log = structlog.get_logger(__name__)

DEFAULT_ZONE = "America/New_York"


def site_zone(session: Session) -> ZoneInfo:
    """The site's zone (zone_named)."""
    return zone_named(session.get_one(SiteSettings, 1).time_zone)


def zone_named(name: str) -> ZoneInfo:
    """A zone by its IANA name. One these rules no longer know, such as one
    a newer tzdata dropped or one set by hand, falls back to the default, so
    no page breaks over it."""
    try:
        return ZoneInfo(name)
    except zoneinfo.ZoneInfoNotFoundError, ValueError:
        log.warning("time zone unknown here, using the default", zone=name)
        return ZoneInfo(DEFAULT_ZONE)


# Deeper than any store's areas nest, so only a loop meets it.
MOST_NESTED = 64


def zone_at(session: Session, location_id: int | None) -> str:
    """The zone a place is in, by name: its own, else the nearest of its
    parents', else the site's (inventory-tng-dp5); the site's for none. One
    query, up its parents, at most MOST_NESTED deep, so parents that loop
    (locations.no_cycle stops that) end the walk."""
    up = (
        select(Location.parent_id, Location.time_zone, literal(0).label("depth"))
        .where(Location.id == location_id)
        .cte("up", recursive=True)
    )
    up = up.union(
        select(Location.parent_id, Location.time_zone, up.c.depth + 1)
        .join(up, Location.id == up.c.parent_id)
        .where(up.c.depth < MOST_NESTED)
    )
    own = (
        select(up.c.time_zone)
        .where(up.c.time_zone.is_not(None))
        .order_by(up.c.depth)
        .limit(1)
        .scalar_subquery()
    )
    site = select(SiteSettings.time_zone).where(SiteSettings.id == 1).scalar_subquery()
    return session.execute(select(func.coalesce(own, site))).scalar_one()


def local(moment: datetime, zone: ZoneInfo) -> datetime:
    """A UTC time as the clock on the wall in `zone` showed it."""
    if moment.tzinfo is None:
        raise ValueError("a time without a zone cannot be shown: store UTC")
    return moment.astimezone(zone)


def show(moment: datetime, zone: ZoneInfo | str) -> str:
    """ "2026-11-02 13:05 EST": to the minute, with the zone's abbreviation,
    so a reader can tell EST from EDT, or CST from EST. `zone` by name is a
    zone an event recorded (zone_named)."""
    if isinstance(zone, str):
        zone = zone_named(zone)
    return f"{local(moment, zone):%Y-%m-%d %H:%M %Z}"


def parse_local(typed: str, zone: ZoneInfo) -> datetime | None:
    """A time typed as the clock on the wall in `zone` showed it, such as a
    datetime-local field sends ("2026-11-02T13:05"), in UTC; None if it is
    not one. In the hour a clock goes back, the first of the two."""
    try:
        moment = datetime.fromisoformat(typed.strip())
    except ValueError:
        return None
    if moment.tzinfo is not None:
        return None
    try:
        return moment.replace(tzinfo=zone).astimezone(UTC)
    except OverflowError:  # Past what a datetime holds, once in UTC.
        return None
