"""The settings this telemetry reads, and what they mean when nobody set one.

A module of its own because both halves need it and neither can import the
other: `logs.py` imports `console.py` to draw a record, so anything they share
has to sit under both. Small, like `hosts.py`, and for the same reason -- a
function of its argument that a test can hold directly.

Standard library only, on purpose -- `refusals` included, which is stdlib too:
`console.py` imports this and has to draw a saved stream with nothing
configured. That constraint is why `missing` lives here rather than beside
`environment.Env`, which applies it to the rest.
"""

import os

from inventory_tng import refusals

DEFAULTS = {
    "DJANGO_LOG_LEVEL": "INFO",
    "DJANGO_LOG_LEVELS": "",
    "DJANGO_LOG_FORMAT": "console",
    "DJANGO_LOG_LAYOUT": "",
    "DJANGO_LOG_CONTEXT": "hidden",
    # The one default here that is not about drawing. `refusals` holds the
    # number and the argument for it; this dict holds every default in one
    # place, so it is named here and defined there.
    "DJANGO_SECURITY_LOG_RATE": refusals.DEFAULT_RATE,
    # Off, here and in every configuration this repository ships. `redaction`
    # holds what the two states mean and what turning it on makes true.
    "TELEMETRY_PERSONAL_DATA": "redacted",
}


def missing(value: str | None) -> bool:
    """Whether a variable says nothing, whether or not it is there.

    The predicate every setting in this application is read through. What it
    buys, and the one case where it makes a refusal stricter rather than
    softer, is on `environment.Env`.
    """
    return value is None or not value.strip()


def setting(name: str, environment: dict[str, str] | None = None) -> str:
    """One telemetry setting, or what it means when nobody set it."""
    value = (environment if environment is not None else os.environ).get(name)
    return DEFAULTS[name] if missing(value) else str(value).strip()
