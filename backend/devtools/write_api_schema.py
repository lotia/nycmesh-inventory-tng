"""Writes frontend/src/api/openapi.json, the API's schema, which the
frontend's types are generated from: `uv run python -m devtools
write-api-schema` (CONTRIBUTING.md, The scan page).

`npm run api` then formats it with Biome and generates the types. A test
fails while the committed copy says something the code does not.
"""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from inventory_tng.app import create_app
from inventory_tng.core.local_env import CLONE
from inventory_tng.core.settings import Settings

SCHEMA = CLONE / "frontend" / "src" / "api" / "openapi.json"
# Never connected to: building the app makes an engine, which connects only
# when first used, and nothing here uses it.
UNUSED_DATABASE = "postgresql://localhost/unused"


def schema() -> dict[str, Any]:
    return create_app(Settings(database_url=UNUSED_DATABASE)).openapi()


def main(argv: Sequence[str], prog: str) -> int:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", nargs="?", type=Path, default=SCHEMA)
    path = parser.parse_args(argv).path
    text = json.dumps(schema(), indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")
    print(f"Wrote {path}")
    return 0
