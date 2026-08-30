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

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.http.request import validate_host
from django.test import Client, override_settings
from environ import Env

from inventory.tests.charts import backend_container, environment, manifests, refused, render, rendered, workloads
from inventory.tests.helpers import BACKEND_DOCKERFILE, shipped
from inventory_tng import forwarded
from inventory_tng.database import DEFAULT_CONNECT_TIMEOUT_SECONDS
from inventory_tng.environment import entries
from inventory_tng.hosts import allowed_hosts

# What a kubelet puts in Host when a probe sets no header of its own: the pod's
# address and the port it dialled. Django strips the port before comparing, so
# the address alone is what has to be allowed -- and asserting against the real
# shape is the point, since an empty Host is refused for a different reason and
# would pass this suite for the wrong one.
POD_ADDRESS = "10.42.0.17"
KUBELET_HOST = f"{POD_ADDRESS}:8000"

# How many queries each probe's request is allowed to make. Not which endpoint
# it asks for: a pairing table would restate the manifest, which is the one
# thing this module's own docstring forbids, and editing the chart and the
# table together would leave every test here green. What inventory-tng-uq6 is
# actually about is the count -- liveness must reach nothing, because a probe
# that runs a query restarts every replica the first time a failover runs long
# -- so the count is what is asserted, against whatever path the chart names.
# That also catches liveness repointed at some future endpoint that grows a
# query of its own, which no pairing table would.
QUERIES_ALLOWED = {"livenessProbe": 0, "readinessProbe": 1}


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


def answer(container: dict[str, Any], host: str, probe: str = "readinessProbe") -> int:
    """What this deployment answers `probe`'s request, carrying `host`.

    The path comes out of the rendered manifest rather than being named here,
    so a probe repointed at something this application does not serve is a
    404 rather than a test still asking about the old path.

    One probe answers for both below, because a host is refused in middleware
    before anything is routed -- the path cannot change that answer, and what
    each path costs to serve is asserted separately.
    """
    asked = container[probe]["httpGet"]["path"]
    with override_settings(ALLOWED_HOSTS=as_the_pod_would_read_it(container)):
        return Client().get(asked, headers={"host": host}).status_code


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
    replaced.
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


def test_neither_probe_asserts_anything_about_the_pod_it_dials() -> None:
    """That they name no host is the fix `backend-deployment.yaml` explains.

    The template says why where it declares them. That each asks a path this
    application serves is asserted below instead, by driving it.
    """
    container = backend_container()
    served = container["ports"][0]

    for probe in QUERIES_ALLOWED:
        asked = container[probe]["httpGet"]
        assert asked.get("httpHeaders") is None, (
            f"the {probe} names a host, which asserts something about the pod; it reaches its own address"
        )
        assert asked["port"] == served["name"]


@pytest.mark.django_db
@pytest.mark.parametrize(("probe", "allowed"), QUERIES_ALLOWED.items())
def test_a_probe_reaches_for_no_more_than_its_own_question_needs(
    probe: str, allowed: int, django_assert_num_queries: Any
) -> None:
    """inventory-tng-uq6, asserted against the chart rather than against a name.

    The bug was not that liveness asked for the wrong path; it was that the
    path it asked for ran a query, so a database that went away took every
    replica with it. This drives whatever path the manifest names and counts
    what it costs, so repointing liveness at the readiness endpoint fails --
    and so does repointing it at some endpoint that has no query today and
    grows one later, which is the same bug arriving by a different road.
    """
    asked = backend_container()[probe]["httpGet"]["path"]

    with django_assert_num_queries(allowed):
        assert Client().get(asked).status_code == 200


# What each probe's timing is, and therefore what docs/deployment.md is
# allowed to say about it. Every field either probe relies on, including the
# ones whose values happen to equal a Kubernetes default: a default that a
# document quotes is a number this repository has taken responsibility for,
# and leaving it out of here is how the document goes wrong on its own.
TIMINGS = {
    "livenessProbe": {
        "initialDelaySeconds": 10,
        "periodSeconds": 20,
        "timeoutSeconds": 5,
        "failureThreshold": 3,
    },
    "readinessProbe": {
        "initialDelaySeconds": 5,
        "periodSeconds": 10,
        "timeoutSeconds": 1,
        "failureThreshold": 3,
    },
}


@pytest.mark.parametrize(("probe", "expected"), TIMINGS.items())
def test_a_probe_waits_exactly_as_long_as_the_documents_say_it_does(probe: str, expected: dict[str, int]) -> None:
    """The arithmetic is quoted in prose, so it is pinned where it is written.

    docs/deployment.md#health-checks tells a deployer how long a wedged pod is
    left alone before it is killed, and how long an unready one takes to leave
    the Service. Both are products of fields here, and neither document nor
    chart would notice the other changing. This is what makes them notice.
    """
    # Compared whole, minus the request itself, rather than by projecting the
    # manifest onto the keys named here: a projection is blind in the
    # direction that matters, because a field ADDED to the chart is not one of
    # the keys and so is never looked at. Give readiness a longer deadline to
    # survive load and the documented figure moves while a projection stays
    # green.
    declared = {field: value for field, value in backend_container()[probe].items() if field != "httpGet"}

    assert declared == expected


# What gunicorn is asked for, and where. Every figure in
# docs/deployment.md#health-checks divides worker-seconds by three, and the
# three are a flag in the image's command line -- a place no manifest mentions
# and nothing else here reads, so that section would go on dividing by a number
# the pod had stopped running. Both files that configure gunicorn are read,
# because the command line names a configuration module and either could carry
# the setting that breaks the model.
GUNICORN_CONF = Path("backend") / "src" / "gunicorn.conf.py"
WORKERS = re.compile(r'"(?:--workers|-w)",\s*"3"')
ON_THE_COMMAND_LINE = re.compile(r'"(?:--worker-class|-k|--timeout|-t)"')
IN_THE_CONFIGURATION = re.compile(r"^\s*(?:worker_class|workers|timeout)\s*=", re.MULTILINE)


def test_a_pull_secret_reaches_every_pod_that_pulls() -> None:
    """All three, and the Job is the one it would be forgotten on.

    Both Deployments are visible when a pull fails -- `ImagePullBackOff` on a
    pod somebody is watching. The Job is not, and migrate-job.yaml says what it
    does instead; inventory-tng-v7g is what that already cost once.

    Rendered only when asked for: the images this repository publishes are
    public, and a chart that named a Secret nobody created would refuse to
    start every pod for the sake of a case most deployments do not have.
    """
    without = [
        f"{document['kind']}/{document['metadata']['name']}"
        for document in manifests()
        if document.get("kind") in ("Deployment", "Job") and "imagePullSecrets" in document["spec"]["template"]["spec"]
    ]
    assert not without, f"{without} name a pull secret nobody asked for, so every pod needs one to exist"

    asked = manifests(**{"image.pullSecrets[0].name": "a-registry-credential"})
    carrying = [
        document["metadata"]["name"]
        for document in asked
        if document.get("kind") in ("Deployment", "Job")
        and document["spec"]["template"]["spec"].get("imagePullSecrets") == [{"name": "a-registry-credential"}]
    ]
    assert len(carrying) == 3, (
        f"only {carrying} carry the pull secret. A pod left out cannot pull from a private registry at all, "
        "and for the migrate Job that is a release which blocks rather than an error"
    )


def test_every_container_the_chart_renders_declares_what_it_needs() -> None:
    """A pod that declares nothing is refused by a namespace with a quota.

    And the migrate Job is the one where that refusal is worst, which is why
    this asserts every container rather than only the two Deployments. The Job
    is a pre-upgrade hook, so a pod that is never admitted is not an error
    about resources: `helm upgrade` waits for a Job that cannot run and gives
    up at its timeout, leaving nothing in the release to look at. That is
    inventory-tng-v7g, and it is the second row of the troubleshooting table
    docs/deployment.md opens with.

    Requests only. A memory limit is a policy this chart does have and a cpu
    limit is one it deliberately does not -- see values.yaml -- so asserting
    the pair would be asserting the opposite of a decision.
    """
    bare = []
    for document in workloads():
        kind = document["kind"]
        for container in document["spec"]["template"]["spec"]["containers"]:
            requests = (container.get("resources") or {}).get("requests") or {}
            if not {"cpu", "memory"} <= set(requests):
                bare.append(f"{kind}/{document['metadata']['name']}:{container['name']}")

    assert not bare, (
        f"{bare} declare no cpu or memory request, so a namespace with a ResourceQuota refuses them unless "
        "a LimitRange fills them in -- and for the migrate Job that is a helm upgrade which blocks until it "
        "times out rather than an error naming the cause"
    )


def test_the_chart_refuses_a_connect_that_outlives_a_readiness_period() -> None:
    """The one relationship in this deployment that spans two files.

    A probe blocked on the database holds a worker until the driver gives up,
    so the bound decides how much of a three-worker pod probe traffic can be
    holding at once. Under the period it is one worker's worth at any instant;
    each further period it is raised past costs another worker, and at three of
    them the pod is serving nobody. docs/deployment.md#health-checks is that
    arithmetic in full.

    A values file is where an operator would raise it, so nothing but a refusal
    at render time can catch it -- and the period the guard measures against is
    asked of the same define the probe is drawn from, so this only has to prove
    the guard is wired to it.
    """
    period = backend_container()["readinessProbe"]["periodSeconds"]

    assert refused(**{"django.databaseConnectTimeoutSeconds": str(period - 1)}) is None, (
        f"the chart refuses {period - 1}s, which is inside a period of {period}s"
    )
    objection = refused(**{"django.databaseConnectTimeoutSeconds": str(period)})
    assert objection is not None, (
        f"the chart renders a connect timeout of {period}s against a readiness probe every {period}s, so a "
        "blocked probe still holds its worker when the next one arrives and they accumulate"
    )
    assert "periodSeconds" in objection


def test_what_a_deployment_configuring_nothing_is_bounded_by() -> None:
    """The other way in, which the guard above cannot see.

    The chart's value is only its default: a release that sets nothing runs on
    the application's, which is not a number `helm` has any view of.
    """
    period = backend_container()["readinessProbe"]["periodSeconds"]

    assert period > DEFAULT_CONNECT_TIMEOUT_SECONDS, (
        f"a process nobody configured waits {DEFAULT_CONNECT_TIMEOUT_SECONDS}s, which a probe every "
        f"{period}s does not outlast"
    )


def test_the_pod_is_handed_the_proxy_list_the_chart_offers_to_set() -> None:
    """A value nothing wires is a knob that does nothing, quietly.

    `values.yaml` offering `django.trustedProxies` and `_helpers.tpl` naming
    `TRUSTED_PROXIES` are two separate facts, and only the second decides what
    a pod reads. Asked of the render for exactly that reason: read out of
    `values.yaml` instead, deleting the `_helpers.tpl` block would leave the
    settings module's empty default in place and this suite green.

    Read through `forwarded.networks` rather than compared as a string, because
    what has to hold is what the application makes of what the manifest
    supplies -- the same move as the probes above. The shipped answer is the
    empty list, which believes no forwarded header from anybody; decision 0023
    is why that is the safe direction rather than merely the current one.
    """
    supplied = rendered()

    assert "TRUSTED_PROXIES" in supplied, (
        "the chart offers django.trustedProxies and hands the pod nothing, so setting it would do nothing"
    )
    listed = entries(Env.parse_value(supplied["TRUSTED_PROXIES"]["value"], list))

    assert forwarded.networks(listed) == [], (
        f"the shipped chart would have a pod believe a forwarded header from {listed}"
    )


def test_the_pod_runs_the_workers_the_arithmetic_divides_by() -> None:
    """The last number that document quotes from outside these manifests.

    `TIMINGS` above says why a figure a document quotes belongs in this file
    whatever else holds it. This is the same argument reaching past the chart:
    drop the count to two, or hand gunicorn threads instead, and every figure
    in that section is wrong with the suite green -- threads worst of all,
    because worker-seconds stop being the model rather than the arithmetic
    coming out differently.
    """
    command = shipped(BACKEND_DOCKERFILE)
    configuration = (settings.REPO_ROOT / GUNICORN_CONF).read_text()

    assert WORKERS.search(command), (
        f"docs/deployment.md#health-checks divides its worker-seconds by three workers, and {BACKEND_DOCKERFILE} no "
        "longer asks for three"
    )
    for where, chosen in (
        (BACKEND_DOCKERFILE, ON_THE_COMMAND_LINE.search(command)),
        (GUNICORN_CONF, IN_THE_CONFIGURATION.search(configuration)),
    ):
        assert chosen is None, (
            f"{where} chooses gunicorn's worker class, worker count or request timeout, so a pod no longer "
            "holds one blocked request per worker and the worker-seconds in "
            "docs/deployment.md#health-checks are not the model for it"
        )


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
    # Through `entries`, because the whole claim here is that the chart and
    # Django answer alike, and a hand-rolled trim beside them would be a third
    # answer nobody compares -- which is the shape of the bug the trim exists
    # for. `inventory_tng.environment` is where that argument lives.
    django_would_answer = validate_host(host, entries(listed.split(",")))

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
