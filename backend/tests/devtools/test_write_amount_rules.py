import json
from pathlib import Path

from devtools.__main__ import main as run_devtools
from inventory_tng.catalogue import amount_rules


def test_writes_the_table(tmp_path: Path) -> None:
    # As the task runs it, here to another file.
    path = tmp_path / "amount-rules.json"
    assert run_devtools(["write-amount-rules", str(path)]) == 0
    assert json.loads(path.read_text(encoding="utf-8")) == amount_rules.table()
