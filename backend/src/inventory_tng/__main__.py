"""Runs the server: `uv run python -m inventory_tng --reload` (CONTRIBUTING.md).

uvicorn is told not to configure logging (`log_config=None`), so its lines
reach our handler like any library's (docs/decisions/0007). It reads
FORWARDED_ALLOW_IPS from the environment itself: set it to the reverse
proxy's address, so the logged client address is the real one.
"""

import argparse
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from inventory_tng.local_env import load_local_env
from inventory_tng.logs import configure_logging
from inventory_tng.settings import Settings

PACKAGE = Path(__file__).parent


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m inventory_tng")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--reload", action="store_true", help="restart when code changes (development)"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="processes, on a server without a cluster",
    )
    args = parser.parse_args(argv)

    load_local_env()
    # This process's own lines (the reloader, the worker supervisor) take our
    # format too. Each worker sets itself up again in create_server(). Read
    # every setting, so a bad one stops the server here, once, and secrets are
    # unmasked here only where a worker would unmask them.
    configure_logging(Settings.from_environ())
    uvicorn.run(
        "inventory_tng.server:create_server",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(PACKAGE)] if args.reload else None,
        workers=args.workers,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
