"""The time zones the app knows: zoneinfo's, from the tzdata package, so
they do not depend on the operating system's (docs/decisions/0013)."""

import functools
import zoneinfo


@functools.cache
def known_zones() -> frozenset[str]:
    """Every zone these rules know, by name, as zoneinfo lists them: not
    the leap-second right/ or posix/ copies an operating system may also
    have. Read once, as the list is long and does not change while the
    server runs."""
    return frozenset(zoneinfo.available_timezones())


def is_time_zone(name: str) -> bool:
    """An IANA zone such as America/New_York that these rules know."""
    return name in known_zones()
