"""A form's own problems with what was typed, found before any save (a
box left empty, a number that is not one), each by its kind, and the
counter of those a page shows (Ali, 2026-11-27, inventory-tng-v8sq).

A form's check says a problem as a Problem, with its kind; web/pages.py's
page(), which every page with a form is drawn by, counts and logs those
it shows, once each, by the page's template and the kind. A rule's words
beside a box are no Problem: core/db_errors.py counts the rule refusing
the change. Loads neither FastAPI nor the templates, so each feature's
forms.py may import it (docs/decisions/0023)."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self, get_args

import structlog
from opentelemetry import metrics

from inventory_tng.core.phrases import Words
from inventory_tng.core.refusals import Refusals

log = structlog.get_logger(__name__)

# What is wrong with a box: left empty, or nothing picked from its list;
# no number, or written with the other decimal separator, grouped as
# thousands, or more decimal places than kept (web/forms.Unreadable); a
# number too big or too small for it; a fraction where it takes whole
# units; too long or too short; not of its form, such as an email address;
# not matching another box or a code; no such row, as a form made by hand
# names; another row's already; or the form's own words for something
# else. The counter's attribute, from this list
# only.
Kind = Literal[
    "required",
    "not_chosen",
    "number",
    "separator",
    "thousands",
    "places",
    "too_big",
    "too_small",
    "not_whole",
    "too_long",
    "too_short",
    "not_valid",
    "mismatch",
    "not_found",
    "taken",
    "other",
]

TEMPLATES = Path(__file__).parent.parent / "templates"
# Each page's template, by its path without .html: the form a problem is
# counted by, from this list only.
FORMS = frozenset(
    str(path.relative_to(TEMPLATES).with_suffix(""))
    for path in TEMPLATES.rglob("*.html")
)


class Problem(str):
    """A form's own problem with a box, in the words its page says, and its
    kind, so the page counts it as it shows it."""

    kind: Kind

    def __new__(cls, words: Words, kind: Kind) -> Self:
        made = super().__new__(cls, str(words))
        made.kind = kind
        return made


def whole_problem(typed: str, words: Words) -> Problem:
    """A box of a whole number refused, in `words`: left empty, or not one
    in its range."""
    return Problem(words, "not_valid" if typed.strip() else "required")


shown = Refusals(
    log,
    metrics.get_meter(__name__).create_counter(
        "inventory.forms.problems",
        unit="{problem}",
        description="A form's own problems with what was typed, found before "
        "any save and shown beside its boxes, by the page's form and the kind "
        "of problem",
    ),
    form=FORMS,
    kind=get_args(Kind),
)


def count_shown(template: str, context: Mapping[str, Any]) -> None:
    """Log and count the Problems the page drawn from `template` shows, by
    box, of its context's `problems`, and its `problem`, one said of the
    whole form: one line for each kind, naming its boxes."""
    by_kind: dict[Kind, list[str]] = {}
    problem = context.get("problem")
    whole = {"problem": problem} if isinstance(problem, Problem) else {}
    for box, words in {**context.get("problems", {}), **whole}.items():
        if isinstance(words, Problem):
            by_kind.setdefault(words.kind, []).append(box)
    for kind, boxes in sorted(by_kind.items()):
        shown(
            "form refused for its boxes",
            form=template.removesuffix(".html"),
            kind=kind,
            count=len(boxes),
            boxes=sorted(boxes),
        )
