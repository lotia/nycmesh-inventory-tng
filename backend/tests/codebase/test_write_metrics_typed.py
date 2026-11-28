"""`ty check` refuses a write measured by hand, before any test runs (Ali,
2026-11-27, inventory-tng-8xs0): a WriteMetric (core/write_metrics.py) has
no add() or record(), only on_commit(), and fields to log need the event
they are logged with. Run on a small file written for it, with the right
calls first and each wrong one on a line of its own."""

import subprocess
from pathlib import Path

from ty import find_ty_bin

BACKEND = Path(__file__).parents[2]
CHECKED = """\
from typing import Any, cast

from sqlalchemy.orm import Session

from inventory_tng.core.write_metrics import WriteMetric

session = cast(Session, None)
saved = WriteMetric(cast(Any, None), cast(Any, None))

saved.on_commit(session, "thing saved", attributes={"how": "one"}, thing_id=4)
saved.on_commit(session, amount=2)
saved.add(1)
saved.record(1)
saved.on_commit(session, thing_id=4)
saved.on_commit("thing saved")
"""


def test_ty_refuses_a_write_measured_by_hand(tmp_path: Path) -> None:
    checked = tmp_path / "checked.py"
    checked.write_text(CHECKED)
    found = subprocess.run(
        [find_ty_bin(), "check", "--output-format", "concise", str(checked)],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [
        line for line in found.stdout.splitlines() if line.startswith(str(checked))
    ]
    refused = sorted({int(line.split(":")[1]) for line in lines})
    assert refused == [12, 13, 14, 15], found.stdout
