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

    `extra` is what the arrangement supplies for itself rather than what an
    operator chose, and there are two kinds of that. In a cluster it is the
    address assigned to the pod, which nobody could have listed in advance --
    docs/deployment.md#health-checks says what fills it and why. Under compose
    it is the opposite case and reaches here for the same reason: names that
    are perfectly well known in advance, but that must survive a developer
    narrowing the list beside them, because one of them is the address the
    container's own healthcheck dials.

    What both have in common is the only property this argument relies on: it
    is ADDED, never substituted. A caller may reduce `listed` to nothing and
    the deployment still answers to what it needs to answer to.

    Both lists are put through `environment.entries`, which is where the
    trimming and its argument live. Called here rather than relied upon,
    because `test_chart.py` hands this function what the chart renders rather
    than what `Env` read, and the two answers have to be one answer.
    """
    return entries([*listed, *extra])
