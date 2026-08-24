"""How long this process waits on a database that is not answering.

Why there is a bound at all, why it is the figure it is, and which way it is
dangerous to move are argued in .env.sample, beside the variable that moves it;
what it must stay under is docs/deployment.md#health-checks. This module
applies it, and holds only what is true of the code rather than of the number.

Two such things. It bounds the dial and nothing after it, so the fault filed as
`inventory-tng-nqoi` is untouched by it. And every request pays it, because
Django's `CONN_MAX_AGE` default is nought and nothing here raises it, so no
connection outlives the request that opened it -- whether one should is
`inventory-tng-jzxu`.

`inventory/tests/test_chart.py` holds the figure against the probe that sets
its ceiling, so neither can move alone.
"""

from typing import Any

from inventory_tng.environment import Env

# Seconds, and the smallest the driver will act on -- .env.sample says what
# each means to a deployer. Anything under the floor is refused here rather
# than raised to it, because a bound quietly replaced by a different bound is
# how somebody comes to believe they have one.
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5
MINIMUM_CONNECT_TIMEOUT_SECONDS = 2

# What this may be given to: `connect_timeout` is libpq's word and no other
# driver's, and sqlite would raise on it at the first query.
POSTGRESQL = "postgresql"


def connect_timeout(value: Any, source: str) -> int:
    """`value` as a wait this can rely on, or a refusal naming where it came from.

    Two settings can supply this one number, so both refusals say which of them
    arrived. Without that the failure is a bare `int()` traceback during
    settings import: every replica in `CrashLoopBackOff` over a message
    mentioning neither the variable nor the chart key that set it.
    """
    try:
        asked = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{source}={value!r} is not a whole number of seconds.") from None
    if asked < MINIMUM_CONNECT_TIMEOUT_SECONDS:
        raise ValueError(
            f"{source}={asked!r} is a wait the driver would not keep: it reads anything below "
            f"{MINIMUM_CONNECT_TIMEOUT_SECONDS} as no limit at all. See docs/deployment.md#health-checks."
        )
    return asked


def bounded(config: dict[str, Any], fallback: Any) -> dict[str, Any]:
    """`config` with a wait it can rely on, keeping anything the URL said.

    `django-environ` renders a URL's query string as `OPTIONS`, so a
    `DATABASE_URL` can arrive carrying `sslmode`, or a `connect_timeout` of its
    own. Those are the more particular instruction of the two and are left
    standing; `fallback` is for a URL that asked for nothing, which is every
    URL this repository ships. Whichever ends up in force is put through the
    same refusals, since a wait nobody can rely on is no better for having been
    typed into a URL.

    A database the option means nothing to is handed back untouched. Which
    engines this application supports is settled elsewhere, and is not a
    question for the module that bounds a connect.
    """
    if POSTGRESQL not in config.get("ENGINE", ""):
        return config
    options = dict(config.get("OPTIONS", {}))
    asked, source = (
        (options["connect_timeout"], "a connect_timeout in DATABASE_URL")
        if "connect_timeout" in options
        else (fallback, "DATABASE_CONNECT_TIMEOUT_SECONDS")
    )
    return {**config, "OPTIONS": {**options, "connect_timeout": connect_timeout(asked, source)}}


def configured(env: Env) -> dict[str, Any]:
    """The `default` database, as `settings.py`'s own reader describes it.

    Handed the settings module's `Env` rather than making one, so the variable
    is declared once, among the knobs, where a reader looking for what this
    process takes from its environment will find it. Declared there as text, so
    that the refusal above is the only thing turning it into a number and can
    therefore say which of the two settings was wrong.
    """
    return bounded(env.db("DATABASE_URL"), env("DATABASE_CONNECT_TIMEOUT_SECONDS"))
