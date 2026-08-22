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


def manifests(**overrides: str) -> list[dict[str, Any]]:
    """Every object the chart renders."""
    return [document for document in yaml.safe_load_all(render(**overrides)) if document]
