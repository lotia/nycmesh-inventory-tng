"""The scan page's bundle: the pages that load it, and /static that serves it
(docs/decisions/0010).

A page's tags come from Vite's manifest, following the entry's imports for
styles and preloads, as Vite's backend integration guide says; with
VITE_DEV_SERVER set, they point at Vite's dev server instead.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import structlog
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import PlainTextResponse, Response
from servestatic import ServeStaticASGI
from starlette.types import ASGIApp

from inventory_tng.pages import templates
from inventory_tng.settings import FrontendConfig, Settings

log = structlog.get_logger(__name__)

router = APIRouter()

STATIC = "/static/"
ASSETS = STATIC + "assets/"
# Where Vite's manifest is, inside dist.
MANIFEST = Path(".vite") / "manifest.json"
# Each page and the entry in frontend/vite.config.ts that it loads.
PAGES = {
    "/scan": ("Scan", "src/scan.tsx"),
    "/register": ("Register this device", "src/register.tsx"),
}


@dataclass(frozen=True)
class Tags:
    """What a page's <head> loads, as URLs."""

    styles: tuple[str, ...] = ()
    preloads: tuple[str, ...] = ()
    scripts: tuple[str, ...] = ()


class Bundle:
    """The tags each page needs. Outside development, read once at startup,
    as /static's files are, so a build is never replaced under a running
    server. In development, read on every page, so a new build shows."""

    def __init__(self, config: FrontendConfig, *, development: bool) -> None:
        self.config = config
        self.development = development
        self.built = None if config.dev_server is not None else self.read()

    def tags(self, entry: str) -> Tags | None:
        """None if there is no usable build."""
        if self.config.dev_server is not None:
            # The dev server serves the sources under the build's base.
            base = self.config.dev_server + STATIC
            return Tags(scripts=(base + "@vite/client", base + entry))
        built = self.read() if self.development else self.built
        return None if built is None else built[entry]

    def read(self) -> dict[str, Tags] | None:
        """Each entry's tags, from the manifest; None if there is no usable
        build."""
        try:
            manifest = json.loads((self.config.dist / MANIFEST).read_text())
            return {
                entry: _from_manifest(manifest, entry) for _, entry in PAGES.values()
            }
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            # Unreadable, half written, from code without every page, or not
            # Vite's: the pages answer as if unbuilt.
            log.warning("frontend manifest unusable", error=type(error).__name__)
            return None


def _from_manifest(manifest: dict[str, dict], entry: str) -> Tags:
    styles: list[str] = []
    preloads: list[str] = []
    seen: set[str] = set()

    def visit(key: str) -> None:
        # Each chunk once, however many import it.
        if key in seen:
            return
        seen.add(key)
        chunk = manifest[key]
        styles.extend(chunk.get("css", ()))
        if key != entry:
            preloads.append(chunk["file"])
        for imported in chunk.get("imports", ()):
            visit(imported)

    visit(entry)
    return Tags(
        styles=tuple(STATIC + file for file in styles),
        preloads=tuple(STATIC + file for file in preloads),
        scripts=(STATIC + manifest[entry]["file"],),
    )


def _page(title: str, entry: str) -> Callable[[Request], Response]:
    def page(request: Request) -> Response:
        page_tags = request.app.state.bundle.tags(entry)
        if page_tags is None:
            return PlainTextResponse("The scan page is not built yet.", status_code=503)
        return templates.TemplateResponse(
            request,
            "page.html",
            {"title": title, "tags": page_tags},
            # The page names this build's files, so it must not outlive it.
            headers={"Cache-Control": "no-cache"},
        )

    return page


for path, (title, entry) in PAGES.items():
    router.add_api_route(
        path, _page(title, entry), methods=["GET", "HEAD"], include_in_schema=False
    )


def install(app: FastAPI, settings: Settings) -> None:
    """Load the build's tags, and serve its files at /static/assets/."""
    config, development = settings.frontend, settings.development
    bundle = app.state.bundle = Bundle(config, development=development)
    # Said once, where it is decided: VITE_DEV_SERVER is honoured by a server
    # you run yourself, and blanked in the app container.
    if config.dev_server is not None:
        log.info("frontend", source="dev_server", at=config.dev_server)
        return
    log.info("frontend", source="build", at=str(config.dist))
    # An unusable manifest has already said so.
    if bundle.built is None and not (config.dist / MANIFEST).exists():
        log.warning("frontend not built", dist=str(config.dist))
    # Development looks for files on each request, so a build made after the
    # server started is served too.
    if bundle.built is None and not development:
        return
    # Only assets/: the manifest beside it is the backend's, not the
    # browser's. Vite names everything in assets/ for its contents, so all of
    # it is cached for good.
    files = ServeStaticASGI(
        None,
        root=config.dist / "assets",
        prefix=ASSETS,
        autorefresh=development,
        immutable_file_test=lambda _path, _url: True,
        # Only this origin's pages load these files.
        allow_all_origins=False,
    )
    # asgiref's scope types, which ServeStatic uses, are narrower than
    # Starlette's for the same thing.
    app.mount(ASSETS.rstrip("/"), cast(ASGIApp, files))
