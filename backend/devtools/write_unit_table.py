"""Writes frontend/src/units.json, the unit table the scan page reads
(inventory_tng.catalogue.units): `mise run regenerate-units` runs it, then
lays the file out as Biome does (CONTRIBUTING.md). A test fails while the
committed copy says something the code does not.
"""

from collections.abc import Sequence

from devtools import json_file
from inventory_tng.catalogue import units
from inventory_tng.core.local_env import CLONE

TABLE = CLONE / "frontend" / "src" / "units.json"


def main(argv: Sequence[str], prog: str) -> int:
    return json_file.write(
        argv, prog=prog, description=__doc__, default=TABLE, content=units.table
    )
