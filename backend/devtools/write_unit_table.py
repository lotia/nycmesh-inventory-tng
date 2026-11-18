"""Writes frontend/src/units.json, the unit table the scan page reads
(inventory_tng.catalogue.units): `mise run regenerate-units` runs it, then
lays the file out as Biome does (CONTRIBUTING.md). A test fails while the
committed copy says something the code does not.
"""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from inventory_tng.catalogue import units
from inventory_tng.core.local_env import CLONE

TABLE = CLONE / "frontend" / "src" / "units.json"


def main(argv: Sequence[str], prog: str) -> int:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", nargs="?", type=Path, default=TABLE)
    path = parser.parse_args(argv).path
    text = json.dumps(units.table(), indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")
    print(f"Wrote {path}")
    return 0
