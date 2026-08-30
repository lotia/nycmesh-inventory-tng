"""Rendering the Helm chart, for the tests that read what it makes.

Here rather than in either caller because two tests ask the same question and a
second answer to it is the drift they exist to catch: `test_chart` holds the
rendered manifests against the application they configure, and
`test_documented_commands` reads object names out of them to check every
`kubectl` line in docs/deployment.md addresses something the chart makes. The
release and the flags decide both answers, so they are known in one place.

Alongside `workbooks.py` and `helpers.py` for the same reason: a module the
tests import, rather than a `test_` module they import each other from.
"""

import subprocess
from functools import cache
from typing import Any

import yaml
from django.conf import settings

CHART = settings.REPO_ROOT / "infra" / "helm" / "inventory-tng"

# The release docs/deployment.md installs. The rendered names and the values
# both depend on it, so a test rendering another one tests another thing.
RELEASE = "inventory-tng"


@cache
def _rendered(overrides: tuple[tuple[str, str], ...]) -> str:
    """One `helm template` run per distinct set of overrides.

    Cached because the render is a pure function of this chart directory and
    these flags, and the suite asks for the same one repeatedly -- the default
    render alone is wanted four times across the two modules that import this.
    A raised `CalledProcessError` is not cached, so a test that renders a
    release expecting to be refused still runs `helm` every time.
    """
    chosen = [arg for name, value in overrides for arg in ("--set", f"{name}={value}")]
    return subprocess.run(
        ["helm", "template", RELEASE, str(CHART), "--set", "image.tag=v0.1.0", *chosen],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def render(**overrides: str) -> str:
    """`helm template` for the documented install, as text.

    `image.tag` is set because the chart has no published image to default to
    yet (`inventory-tng-qe7`); everything else comes from `values.yaml`, so
    what is rendered here is what an operator following the document gets.
    """
    return _rendered(tuple(sorted(overrides.items())))


def refused(**overrides: str) -> str | None:
    """What the chart said about `overrides`, if it refused to render them.

    Here rather than in the test that asks, because the chart has more than one
    render-time guard and each is proved the same way: give it the values, and
    read the complaint. `None` is a chart that rendered.
    """
    try:
        render(**overrides)
    except subprocess.CalledProcessError as objection:
        return str(objection.stderr)
    return None


def manifests(**overrides: str) -> list[dict[str, Any]]:
    """Every object the chart renders."""
    return [document for document in yaml.safe_load_all(render(**overrides)) if document]


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
