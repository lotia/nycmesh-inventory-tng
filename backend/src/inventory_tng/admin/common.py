"""What the administrator pages share."""

from datetime import UTC, datetime
from typing import Any, cast

from inventory_tng.pages import templates

ADMIN = "/admin"
NAME_MAX = 200

# The sections, in the order the navigation shows them: (path, label).
SECTIONS: list[tuple[str, str]] = []


def section(path: str, label: str) -> str:
    """Add a section to the navigation; returns its path."""
    SECTIONS.append((path, label))
    return path


_globals = cast(dict[str, Any], templates.env.globals)
_globals["sections"] = SECTIONS
_globals["name_max"] = NAME_MAX


def now() -> datetime:
    return datetime.now(UTC)


def name_problem(name: str) -> str | None:
    if not 1 <= len(name) <= NAME_MAX:
        return f"Give it a name of up to {NAME_MAX} characters."
    return None
