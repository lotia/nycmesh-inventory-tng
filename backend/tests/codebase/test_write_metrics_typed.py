"""`ty check` refuses a write measured by hand, before any test runs (Ali,
2026-11-27, inventory-tng-8xs0): a WriteMetric (core/write_metrics.py) has
no add() or record(), only on_commit(), and fields to log need the event
they are logged with; a refusal's written() is one too. Run on a small
file written for it, with the right calls first and each wrong one on a
line of its own."""

from pathlib import Path

from helpers.ty_checked import refused_lines

CHECKED = """\
from typing import Any, cast

from sqlalchemy.orm import Session

from inventory_tng.core.refusals import Refusals
from inventory_tng.core.write_metrics import WriteMetric

session = cast(Session, None)
saved = WriteMetric(cast(Any, None), cast(Any, None))
signed_in = cast(Refusals, None).written()

saved.on_commit(session, "thing saved", attributes={"how": "one"}, thing_id=4)
saved.on_commit(session, amount=2)
saved.add(1)
saved.record(1)
saved.on_commit(session, thing_id=4)
saved.on_commit("thing saved")
signed_in.add(1)
"""


def test_ty_refuses_a_write_measured_by_hand(tmp_path: Path) -> None:
    assert refused_lines(CHECKED, tmp_path) == [14, 15, 16, 17, 18]
