"""The time zones the app knows: zoneinfo's, from the tzdata package, so
they do not depend on the operating system's (docs/decisions/0013).

zoneinfo looks in the operating system's zone folders before tzdata, so
importing this module empties that search path, for the whole process.
Every use of a zone comes through here first (models imports it, and
local_time models), so the server, the command line, migrations and
tests all read tzdata alone. In code, not PYTHONTZPATH, so no way of
starting the app can leave it out."""

import functools
import zoneinfo

zoneinfo.reset_tzpath(to=())


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


@functools.cache
def _choices() -> tuple[str, ...]:
    return tuple(
        sorted(
            name
            for name in known_zones()
            if name == "UTC"
            or ("/" in name and not name.startswith(("Etc/", "SystemV/", "US/")))
        )
    )


def zones(current: str | None = None) -> list[str]:
    """The zones to pick from, by name: the usual ones, and `current` first
    if it is not among them, so a page never quietly picks another for it."""
    names = _choices()
    return [current, *names] if current and current not in names else list(names)
