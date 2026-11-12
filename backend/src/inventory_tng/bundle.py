"""The frontend's bundle: the scan and registration pages that load it, the
stylesheet every page shares, and /static that serves them (docs/decisions/0010
and 0014).

A page's tags come from Vite's manifest, following the entry's imports for
styles and preloads, as Vite's backend integration guide says; with
VITE_DEV_SERVER set, they point at Vite's dev server instead.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import structlog
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
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
# Beside it, which build this is (frontend/vite.config.ts, BUILD): when it
# was made, in UTC, to the millisecond, so a later build sorts after an
# earlier one as text (page_builds.py).
BUILD_FILE = Path(".vite") / "build.json"
BUILD_ID = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z")
# Each page and the entry in frontend/vite.config.ts that it loads.
SCAN = "/scan"
PAGES = {
    SCAN: ("Scan", "src/scan.tsx"),
    "/register": ("Register this device", "src/register.tsx"),
}
# The stylesheet the server-rendered pages load: an entry of its own.
STYLES = "src/styles.css"
# Their script: what they do beyond plain HTML (docs/decisions/0019).
SCRIPT = "src/admin.ts"


@dataclass(frozen=True)
class Tags:
    """What a page's <head> loads, as URLs."""

    styles: tuple[str, ...] = ()
    preloads: tuple[str, ...] = ()
    scripts: tuple[str, ...] = ()


class Bundle:
    """The tags each page needs. Outside development, read once at startup,
    as /static's files are, so a build is never replaced under a running
    server. In development, read again whenever the manifest changes, so a
    new build shows."""

    def __init__(self, config: FrontendConfig, *, development: bool) -> None:
        self.config = config
        self.development = development
        self.built: dict[str, Tags] | None = None
        # The build's id (BUILD_FILE), read with it; None without one.
        self.build: str | None = None
        # The manifest's modification time and size when self.built was read.
        self.read_at: tuple[int, int] | None = None
        if config.dev_server is None:
            self.reread()

    def tags(self, entry: str) -> Tags | None:
        """None if there is no usable build."""
        if self.config.dev_server is not None:
            # The dev server serves the sources under the build's base.
            base = self.config.dev_server + STATIC
            return Tags(scripts=(base + "@vite/client", base + entry))
        if self.development:
            self.reread()
        return None if self.built is None else self.built[entry]

    def build_id(self) -> str | None:
        """The id of the build the pages come from; None from the dev
        server, or without a usable build."""
        if self.config.dev_server is not None:
            return None
        if self.development:
            self.reread()
        return None if self.built is None else self.build

    def reread(self) -> None:
        """Read the manifest again if it changed since it was last read."""
        try:
            found = (self.config.dist / MANIFEST).stat()
            changed_at = (found.st_mtime_ns, found.st_size)
        except OSError:
            changed_at = None
        if changed_at != self.read_at:
            # The build first: another request that sees read_at changed
            # must find the build that goes with it.
            built = None if changed_at is None else self.read()
            self.build = None if built is None else self.read_build()
            self.built = built
            self.read_at = changed_at

    def read(self) -> dict[str, Tags] | None:
        """Each entry's tags, from the manifest; None if there is no usable
        build."""
        try:
            manifest = json.loads((self.config.dist / MANIFEST).read_text())
            entries = [entry for _, entry in PAGES.values()] + [STYLES, SCRIPT]
            return {entry: _from_manifest(manifest, entry) for entry in entries}
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            # Unreadable, half written, from code without every page, or not
            # Vite's: the pages answer as if unbuilt.
            log.warning("frontend manifest unusable", error=type(error).__name__)
            return None

    def read_build(self) -> str | None:
        """The build's id, from BUILD_FILE; None if it has none, as a build
        from before builds had ids would not."""
        try:
            build = json.loads((self.config.dist / BUILD_FILE).read_text())["build"]
        except OSError, ValueError, KeyError, TypeError:
            return None
        return build if isinstance(build, str) and BUILD_ID.fullmatch(build) else None


def _from_manifest(manifest: dict[str, dict], entry: str) -> Tags:
    # An entry that is only a stylesheet, as Vite wrote it.
    if manifest[entry]["file"].endswith(".css"):
        return Tags(styles=(STATIC + manifest[entry]["file"],))
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
            {"title": title, "tags": page_tags, "brand_home": SCAN},
            # The page names this build's files, so it must not outlive it.
            headers={"Cache-Control": "no-cache"},
        )

    return page


def shared_styles(request: Request) -> Tags:
    """What a server-rendered page loads for the shared stylesheet: the
    built file, or in development the dev server's, which reloads it.
    Nothing without a build: the page is plain HTML then."""
    return request.app.state.bundle.tags(STYLES) or Tags()


def shared_script(request: Request) -> Tags:
    """What a server-rendered page loads for its script, as shared_styles."""
    return request.app.state.bundle.tags(SCRIPT) or Tags()


# Jinja types its globals by its own defaults' types; any value will do.
_globals = cast(dict[str, Any], templates.env.globals)
_globals["shared_styles"] = shared_styles
_globals["shared_script"] = shared_script


@router.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def root() -> RedirectResponse:
    """Volunteers' phones are who come here; administrators keep /admin."""
    return RedirectResponse(SCAN, status_code=302)


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
    # The build's id is what page_builds.OLDEST_BUILD names.
    log.info("frontend", source="build", at=str(config.dist), build=bundle.build)
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
