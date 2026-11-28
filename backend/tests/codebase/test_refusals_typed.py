"""`ty check` refuses a refusal's reason, a rule's name or a form
problem's kind that is not on its list, before any test runs (Ali,
2026-11-27, inventory-tng-vj5h): each is a Literal (core/refusals.py,
core/rule_names.py's RuleName, web/form_problems.py's Kind). Run on a
small file written for it, with one call of each right and one wrong."""

from pathlib import Path

from helpers.ty_checked import refused_lines

CHECKED = """\
from typing import Any, Literal, Unpack, cast

from inventory_tng.core.db_errors import enforce
from inventory_tng.core.refusals import Fields, refusals
from inventory_tng.web.form_problems import Problem


@refusals(cast(Any, None), cast(Any, None))
def refused(event: str, *, reason: Literal["listed"], **fields: Unpack[Fields]) -> None: ...


refused("refused", reason="listed", item_id=1)
enforce("unique")
Problem("words", "required")
refused("refused", reason="not_listed")
enforce("no_such_rule")
Problem("words", "no_such_kind")
refused("refused", reason="listed", level="loud")
refused("refused")
"""


def test_ty_refuses_a_value_not_on_its_list(tmp_path: Path) -> None:
    assert refused_lines(CHECKED, tmp_path) == [15, 16, 17, 18, 19]
