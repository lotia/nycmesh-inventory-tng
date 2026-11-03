"""pyproject.toml's dependencies give a floor at a minor version only
(CONTRIBUTING.md, Dependency updates): uv.lock pins what is installed, so a
floor says only what the code needs. uv add writes a patch too; trim it."""

import tomllib

from packaging.requirements import Requirement
from packaging.version import Version

from inventory_tng.local_env import CLONE

PYPROJECT = CLONE / "backend" / "pyproject.toml"


def minor_floor(requirement: str) -> bool:
    """>=MAJOR.MINOR; >=0.0.PATCH, with no minor to name; or >=0.MINORb0, for
    a series released only as betas."""
    specifiers = list(Requirement(requirement).specifier)
    if [spec.operator for spec in specifiers] != [">="]:
        return False
    version = Version(specifiers[0].version)
    if version.pre is not None:
        return version.pre == ("b", 0) and len(version.release) == 2
    return len(version.release) == 2 or version.release[:2] == (0, 0)


def test_every_dependency_has_a_minor_floor() -> None:
    project = tomllib.loads(PYPROJECT.read_text())
    requirements = project["project"]["dependencies"] + [
        requirement
        for group in project["dependency-groups"].values()
        for requirement in group
        if isinstance(requirement, str)  # Not an {include-group = ...}.
    ]
    assert [r for r in requirements if not minor_floor(r)] == []
