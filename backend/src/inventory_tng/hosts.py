"""Which hostnames this deployment answers to.

One function, in its own module, because two callers have to agree about it
exactly: `settings.py`, which computes `ALLOWED_HOSTS` for a running process,
and `inventory/tests/test_chart.py`, which asks what the chart's environment
would come to. A test that worked the answer out for itself would be a second
opinion, and the first version of that test held one -- it stripped whitespace
`settings.py` did not, and hid a live bug for as long as it stood.
"""


def allowed_hosts(listed: list[str], extra: list[str]) -> list[str]:
    """The hostnames to accept, from the configured list and the deployment's.

    Both arrive from the environment already split on commas by
    `django-environ`, which splits and does nothing else -- so `"a, b"`, the
    way anybody writes a list of two, yields `" b"`. That matches no request
    and would refuse that host for ever, invisibly, because the space does not
    show in a values file. Stripped here, once, for both.

    `extra` is what only the running deployment can know: addresses assigned
    to it that nobody could have listed in advance. Empty everywhere but a
    cluster. docs/deployment.md#health-checks says what fills it and why.
    """
    return [host.strip() for host in [*listed, *extra] if host.strip()]
