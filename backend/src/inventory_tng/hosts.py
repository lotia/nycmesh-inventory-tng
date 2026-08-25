"""Which hostnames this deployment answers to.

One function, in its own module, because two callers have to agree about it
exactly: `settings.py`, which computes `ALLOWED_HOSTS` for a running process,
and `inventory/tests/test_chart.py`, which asks what the chart's environment
would come to. A test that worked the answer out for itself would be a second
opinion, and the first version of that test held one -- it stripped whitespace
`settings.py` did not, and hid a live bug for as long as it stood.
"""

from inventory_tng.environment import entries


def allowed_hosts(listed: list[str], extra: list[str]) -> list[str]:
    """The hostnames to accept, from the configured list and the deployment's.

    `extra` is what only the running deployment can know: addresses assigned
    to it that nobody could have listed in advance. Empty everywhere but a
    cluster. docs/deployment.md#health-checks says what fills it and why.

    Both lists are put through `environment.entries`, which is where the
    trimming and its argument live. Called here rather than relied upon,
    because `test_chart.py` hands this function what the chart renders rather
    than what `Env` read, and the two answers have to be one answer.
    """
    return entries([*listed, *extra])
