"""Showing times in the site's time zone (docs/decisions/0013).

Every time is stored, compared and sent in UTC. Only what people read is
turned into local time, here, with zoneinfo's rules for the zone, which know
when daylight saving starts and ends. The tzdata package ships those rules,
so they do not depend on the operating system's.
"""

import zoneinfo
from datetime import datetime
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy.orm import Session

from inventory_tng.models import SiteSettings

log = structlog.get_logger(__name__)

DEFAULT_ZONE = "America/New_York"


def is_time_zone(name: str) -> bool:
    """An IANA zone such as America/New_York, not a path or an offset."""
    return name in zoneinfo.available_timezones()


def site_zone(session: Session) -> ZoneInfo:
    """The site's zone. One PostgreSQL knows but these rules do not (their
    lists can differ) falls back to the default, so no page breaks over it."""
    name = session.get_one(SiteSettings, 1).time_zone
    try:
        return ZoneInfo(name)
    except zoneinfo.ZoneInfoNotFoundError, ValueError:
        log.warning("site time zone unknown here, using the default")
        return ZoneInfo(DEFAULT_ZONE)


def local(moment: datetime, zone: ZoneInfo) -> datetime:
    """A UTC time as the clock on the wall in `zone` showed it."""
    if moment.tzinfo is None:
        raise ValueError("a time without a zone cannot be shown: store UTC")
    return moment.astimezone(zone)


def show(moment: datetime, zone: ZoneInfo) -> str:
    """ "2026-11-02 13:05 EST": to the minute, with the zone's abbreviation,
    so a reader can tell EST from EDT."""
    return f"{local(moment, zone):%Y-%m-%d %H:%M %Z}"
