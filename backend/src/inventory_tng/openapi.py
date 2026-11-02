"""Writes the API's schema, which the frontend's types are generated from:
`uv run python -m inventory_tng.openapi` (CONTRIBUTING.md, The scan page).

`npm run api` then formats it with Biome and generates the types. A test
fails while the committed copy says something the code does not.
"""

import json
from pathlib import Path
from typing import Any

from inventory_tng.app import create_app
from inventory_tng.local_env import CLONE
from inventory_tng.settings import Settings

SCHEMA = CLONE / "frontend" / "src" / "api" / "openapi.json"
# Never connected to: building the app makes an engine, which connects only
# when first used, and nothing here uses it.
UNUSED_DATABASE = "postgresql://localhost/unused"


def schema() -> dict[str, Any]:
    return create_app(Settings(database_url=UNUSED_DATABASE)).openapi()


def main(path: Path = SCHEMA) -> None:
    text = json.dumps(schema(), indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
