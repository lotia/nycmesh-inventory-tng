"""Writes frontend/src/amount-rules.json, which units are counted each and
which are lengths, as the scan page reads them
(inventory_tng.catalogue.amount_rules): `mise run regenerate-amount-rules`
runs it, then lays the file out as Biome does (CONTRIBUTING.md). A test
fails while the committed copy says something the code does not.
"""

from collections.abc import Sequence

from devtools import json_file
from inventory_tng.catalogue import amount_rules
from inventory_tng.core.local_env import CLONE

TABLE = CLONE / "frontend" / "src" / "amount-rules.json"


def main(argv: Sequence[str], prog: str) -> int:
    return json_file.write(
        argv, prog=prog, description=__doc__, default=TABLE, content=amount_rules.table
    )
