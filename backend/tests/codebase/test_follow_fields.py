"""Every frontend script that shows something from a field's value follows
the field through `follow` (frontend/src/admin/follow-fields.ts), which
checks it again on pageshow: a value the browser puts back by itself, on
Back, fires no input or change event (inventory-tng-r86h). So no other
module listens for input or change itself, but for the search box's
suggestions, left out below."""

import re

import pytest

from inventory_tng.core.local_env import CLONE

pytestmark = pytest.mark.clone

SOURCE = CLONE / "frontend/src"
HELPER = SOURCE / "admin/follow-fields.ts"
# The search box's suggestions follow what is typed, not what is put back:
# a value restored on Back must not open the list.
LEFT_OUT = {SOURCE / "admin/combobox.ts"}
# A listener added, or set as the field's oninput or onchange.
LISTENS = re.compile(
    r"""addEventListener\(\s*["'`](?:input|change)["'`]"""
    r"""|\.on(?:input|change)\s*=(?!=)"""
)


def test_only_follow_listens_for_input_or_change() -> None:
    sources = [*SOURCE.rglob("*.ts"), *SOURCE.rglob("*.tsx")]
    listening = [
        path.relative_to(SOURCE).as_posix()
        for path in sources
        if ".test." not in path.name
        and path not in {HELPER, *LEFT_OUT}
        and LISTENS.search(path.read_text())
    ]
    assert listening == [], (
        "follow the field with follow (frontend/src/admin/follow-fields.ts),"
        " which checks it again on pageshow"
    )


def test_each_left_out_still_listens() -> None:
    # So the pattern still finds a listener as the code writes one, and
    # none is left out that no longer needs to be.
    for path in LEFT_OUT:
        assert LISTENS.search(path.read_text()), path
