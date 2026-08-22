"""The chart's manifests, held against the application they configure.

`helm lint` says a chart is well formed and `helm template` says it renders.
Neither can say whether what it renders is something this application will
answer, and that gap cost a deployment: every backend pod failed its own
liveness probe with a 400 and was killed for it, because the kubelet dials a
pod by address and Django refuses a host it was not told about. A suite of
string comparisons would not have caught it either, since both halves were
individually correct -- the probe asked for the pod, and `ALLOWED_HOSTS` named
the site.

So the assertions here are not that two strings match. They render the chart,
take the request its probe would make and the environment the same manifest
supplies, and ask Django. Nothing restates a rule that Django, `helm` or
`django-environ` already owns: the first attempt at this file did restate one
-- it stripped whitespace the settings module did not -- and that single
kindness hid a second bug for as long as it stood.

What no probe can check is checked at render time instead. A probe reaches the
pod by its own address, so it goes green whatever `ingress.host` is; only the
chart can refuse a release whose ingress sends a name Django will not answer.
"""

import subprocess
from typing import Any

import pytest
from django.http.request import validate_host
from django.test import Client, override_settings
from django.urls import reverse
from environ import Env

from inventory.tests.charts import manifests, render
from inventory_tng.hosts import allowed_hosts

# What a kubelet puts in Host when a probe sets no header of its own: the pod's
# address and the port it dialled. Django strips the port before comparing, so
# the address alone is what has to be allowed -- and asserting against the real
# shape is the point, since an empty Host is refused for a different reason and
# would pass this suite for the wrong one.
POD_ADDRESS = "10.42.0.17"
KUBELET_HOST = f"{POD_ADDRESS}:8000"


def backend_container(**overrides: str) -> dict[str, Any]:
    """The one container the probes belong to."""
    deployments = [
        document
        for document in manifests(**overrides)
        if document["kind"] == "Deployment" and document["metadata"]["name"].endswith("-backend")
    ]
    assert len(deployments) == 1, "the chart is expected to render exactly one backend Deployment"
    containers = deployments[0]["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 1, "the chart is expected to render exactly one backend container"
    return containers[0]


def environment(container: dict[str, Any]) -> dict[str, Any]:
    """The container's `env`, by name, values and `valueFrom` alike."""
    return {entry["name"]: entry for entry in container["env"]}


def as_the_pod_would_read_it(container: dict[str, Any]) -> list[str]:
    """`ALLOWED_HOSTS` as the running pod computes it.

    Not a reimplementation: `django-environ` splits the value exactly as it
    would in the pod, and `allowed_hosts` is the same function `settings.py`
    calls. Nothing here decides anything, which is the point -- the first
    version of this helper did decide, differently, and hid a bug.
    """
    supplied = environment(container)
    assert supplied["DJANGO_EXTRA_ALLOWED_HOSTS"]["valueFrom"]["fieldRef"]["fieldPath"] == "status.podIP", (
        "the pod's address has to come from the pod; a value written into a chart cannot be its address"
    )
    listed = Env.parse_value(supplied["DJANGO_ALLOWED_HOSTS"]["value"], list)
    return allowed_hosts(listed, [POD_ADDRESS])


def answer(container: dict[str, Any], host: str) -> int:
    """What this deployment answers a request carrying `host`."""
    with override_settings(ALLOWED_HOSTS=as_the_pod_would_read_it(container)):
        return Client().get(reverse("healthz"), headers={"host": host}).status_code


@pytest.mark.django_db
@pytest.mark.parametrize(
    "listed",
    [
        "inventory.nycmesh.net",
        "first.example.org\\, second.example.org",
        # The shape that broke the first attempt at this fix: a wildcard is what
        # an operator reaches for when they suspect a host problem, and the
        # version this replaced answered it with a 400.
        "*",
        # Neither of these is a host Django can compare against, and neither has
        # to be: no probe asks for whatever is written here any more.
        "inventory.example.org:8443",
        ".example.org",
    ],
)
def test_a_probe_is_answered_whatever_allowed_hosts_says(listed: str) -> None:
    """The bug, stated as the thing that has to stay true.

    Over every value that has broken this or plausibly could, because a probe
    that depends on the shape of `django.allowedHosts` is the mistake this
    replaced. One request covers both probes: they ask for the same path with
    the same headers, and that they do is asserted next door.
    """
    # The ingress is off because its own guard would refuse most of these
    # values, for a reason that has nothing to do with what a probe does.
    container = backend_container(**{"django.allowedHosts": listed, "ingress.enabled": "false"})

    assert answer(container, KUBELET_HOST) == 200, (
        f"both probes would be answered 400 and every pod would fail them, with django.allowedHosts={listed!r}"
    )


@pytest.mark.django_db
def test_a_host_nobody_allowed_is_still_refused() -> None:
    """So the fix is a fix and not a widening.

    Letting the pod answer to its own address must not let it answer to
    anything; if it did, the probes passing would mean nothing.
    """
    assert answer(backend_container(), "evil.example.net") == 400


@pytest.mark.django_db
def test_the_hostname_a_browser_uses_is_answered_too() -> None:
    """The pod's address is added to that list, never substituted for it."""
    assert answer(backend_container(), "inventory.nycmesh.net") == 200


def test_both_probes_ask_the_pod_for_a_path_and_port_it_serves() -> None:
    """Everything about the probes that a host assertion cannot see.

    That they name no host is the fix itself, for the reason
    `backend-deployment.yaml` gives where they are declared. That they ask for a
    path this application serves is the failure nothing else here would notice:
    every host assertion above passes against a probe pointed at a 404.
    """
    container = backend_container()
    served = container["ports"][0]

    for probe in ("livenessProbe", "readinessProbe"):
        asked = container[probe]["httpGet"]
        assert asked.get("httpHeaders") is None, (
            f"the {probe} names a host, which asserts something about the pod; it reaches its own address"
        )
        assert asked["path"] == reverse("healthz"), (
            f"the {probe} asks for {asked['path']}, which this application does not serve"
        )
        assert asked["port"] == served["name"]


def test_an_ingress_host_no_allowed_host_covers_is_refused_at_render_time() -> None:
    """The failure no probe can see, so the chart has to.

    The probes reach the pod by its own address and go green; meanwhile nginx
    forwards the browser's Host untouched and Django refuses every request
    through the ingress. Two pods Ready and a site that serves its shell and no
    data.
    """
    with pytest.raises(subprocess.CalledProcessError) as refused:
        render(**{"ingress.host": "inv.example.org"})

    assert "django.allowedHosts" in refused.value.stderr
    assert "inv.example.org" in refused.value.stderr


@pytest.mark.parametrize(
    ("host", "listed"),
    [
        ("inv.example.org", "*"),
        ("inv.example.org", "inv.example.org"),
        ("inv.example.org", ".example.org"),
        # The apex against a leading-dot pattern, and a pattern whose case does
        # not match. Django serves both; the first version of this guard
        # refused both, because it had copied two-thirds of the rule.
        ("example.org", ".example.org"),
        ("inv.example.org", "Inv.Example.org"),
        # And the ones that genuinely are not covered, so the agreement below
        # is not a guard that says yes to everything.
        ("inv.example.org", "other.example.org"),
        ("inv.example.org", ".other.example.org"),
        ("evil-example.org", ".example.org"),
    ],
)
def test_the_chart_covers_an_ingress_host_exactly_when_django_would(host: str, listed: str) -> None:
    """The guard restates Django's rule, so it is held against Django's rule.

    `_helpers.tpl` cannot call `validate_host`, so it reimplements it -- and a
    reimplementation nobody compares is a rule with two answers. This asks both
    and requires the same one. A guard stricter than Django is the worse
    failure of the two it could have: nobody debugs a chart that says no to
    something correct.
    """
    django_would_answer = validate_host(host, [pattern.strip() for pattern in listed.split(",")])

    try:
        render(**{"ingress.host": host, "django.allowedHosts": listed})
    except subprocess.CalledProcessError as refused:
        chart_renders = False
        assert "django.allowedHosts" in refused.stderr
    else:
        chart_renders = True

    assert chart_renders == django_would_answer, (
        f"the chart {'renders' if chart_renders else 'refuses'} ingress.host={host!r} against "
        f"django.allowedHosts={listed!r}, and Django would {'answer' if django_would_answer else 'refuse'} it"
    )
