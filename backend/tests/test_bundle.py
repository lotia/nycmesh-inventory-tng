import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from servestatic.compress import Compressor

from inventory_tng.bundle import (
    BUILD_FILE,
    MANIFEST,
    PAGES,
    SCAN,
    STYLES,
    Bundle,
    Tags,
)
from inventory_tng.settings import FrontendConfig

# The shape `vite build` writes: two entries sharing a chunk that carries the
# stylesheets, a chunk only the scan page imports, and the shared stylesheet,
# an entry of its own that the shared chunk also names.
BUILD = {
    "src/scan.tsx": {
        "file": "assets/scan-Bk6ZGfA8.js",
        "isEntry": True,
        "imports": ["_shared-DeiyL5Lk.js", "_cart-Q1w2e3R4.js"],
    },
    "src/register.tsx": {
        "file": "assets/register-CNMjCjPX.js",
        "isEntry": True,
        "imports": ["_shared-DeiyL5Lk.js"],
    },
    "_cart-Q1w2e3R4.js": {
        "file": "assets/cart-Q1w2e3R4.js",
        "imports": ["_shared-DeiyL5Lk.js"],
        "css": ["assets/cart-T5y6u7I8.css"],
    },
    "_shared-DeiyL5Lk.js": {
        "file": "assets/shared-DeiyL5Lk.js",
        "css": ["assets/shared-qHQdyY8N.css", "assets/styles-BXvLJlQA.css"],
    },
    "src/styles.css": {
        "file": "assets/styles-BXvLJlQA.css",
        "src": "src/styles.css",
        "isEntry": True,
    },
    "src/admin.ts": {
        "file": "assets/admin-Hq3sV8kD.js",
        "src": "src/admin.ts",
        "isEntry": True,
    },
}
# Its id, as vite.config.ts writes it beside the manifest.
BUILD_ID = "2026-11-12T10:00:00.000Z"
SCRIPT = b"export const answer = 42;\n" * 40
WASM = b"\0asm\1\0\0\0" + b"\0" * 2000


def build(dist: Path) -> None:
    """A build as the container makes it: Vite's files, each beside its
    Brotli and gzip copies, from the compressor the container runs."""
    (dist / MANIFEST).parent.mkdir(parents=True)
    (dist / MANIFEST).write_text(json.dumps(BUILD))
    (dist / BUILD_FILE).write_text(json.dumps({"build": BUILD_ID}))
    (dist / "assets").mkdir()
    for name, data in [("scan-Bk6ZGfA8.js", SCRIPT), ("decoder-9e1f3a2b.wasm", WASM)]:
        (dist / "assets" / name).write_bytes(data)
        Compressor(use_zstd=False, quiet=True).compress(dist / "assets" / name)


def assert_not_built(response: Response) -> None:
    """The scan page's own 503, saying there is no build."""
    assert (response.status_code, response.text) == (
        503,
        "The scan page is not built yet.",
    )


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    build(tmp_path)
    return tmp_path


@pytest.fixture
def pages(
    make_app_without_database: Callable[..., FastAPI], dist: Path
) -> Iterator[TestClient]:
    with TestClient(
        make_app_without_database(frontend=FrontendConfig(dist=dist))
    ) as client:
        yield client


def test_an_entry_loads_its_imports_styles_and_preloads_them(dist: Path) -> None:
    bundle = Bundle(FrontendConfig(dist=dist), development=False)
    assert bundle.tags("src/scan.tsx") == Tags(
        styles=(
            "/static/assets/shared-qHQdyY8N.css",
            "/static/assets/styles-BXvLJlQA.css",
            "/static/assets/cart-T5y6u7I8.css",
        ),
        preloads=(
            "/static/assets/shared-DeiyL5Lk.js",
            "/static/assets/cart-Q1w2e3R4.js",
        ),
        scripts=("/static/assets/scan-Bk6ZGfA8.js",),
    )


def test_a_build_is_known_by_its_id(dist: Path) -> None:
    assert Bundle(FrontendConfig(dist=dist), development=False).build_id() == BUILD_ID


@pytest.mark.parametrize(
    "written",
    [None, "not json", "[]", "{}", '{"build": 12}', '{"build": "yesterday"}'],
)
def test_a_build_without_a_usable_id_has_none(dist: Path, written: str | None) -> None:
    """As a build from before ids, or a file not Vite's: its pages still
    load."""
    if written is None:
        (dist / BUILD_FILE).unlink()
    else:
        (dist / BUILD_FILE).write_text(written)
    bundle = Bundle(FrontendConfig(dist=dist), development=False)
    assert bundle.build_id() is None
    assert bundle.tags(STYLES) is not None


def test_no_build_and_the_dev_server_have_no_id(dist: Path, tmp_path: Path) -> None:
    unbuilt = tmp_path / "unbuilt"
    assert Bundle(FrontendConfig(dist=unbuilt), development=False).build_id() is None
    config = FrontendConfig(dist=dist, dev_server="http://localhost:5173/")
    assert Bundle(config, development=True).build_id() is None


def test_the_shared_stylesheet_is_its_own_entry(dist: Path) -> None:
    bundle = Bundle(FrontendConfig(dist=dist), development=False)
    assert bundle.tags(STYLES) == Tags(styles=("/static/assets/styles-BXvLJlQA.css",))


def test_server_rendered_pages_load_the_shared_stylesheet_and_script(
    pages: TestClient, make_app_without_database: Callable[..., FastAPI]
) -> None:
    page = pages.get("/admin/sign-in").text
    assert '<link rel="stylesheet" href="/static/assets/styles-BXvLJlQA.css">' in page
    assert '<script type="module" src="/static/assets/admin-Hq3sV8kD.js">' in page
    # Without a build, a plain page.
    with TestClient(make_app_without_database()) as unbuilt:
        plain = unbuilt.get("/admin/sign-in").text
        assert "stylesheet" not in plain
        assert "/static/" not in plain


def test_the_dev_server_serves_the_sources_instead(dist: Path) -> None:
    config = FrontendConfig(dist=dist, dev_server="http://localhost:5173/")
    assert Bundle(config, development=True).tags("src/register.tsx") == Tags(
        scripts=(
            "http://localhost:5173/static/@vite/client",
            "http://localhost:5173/static/src/register.tsx",
        )
    )


@pytest.mark.parametrize(
    ("path", "script"),
    [
        ("/scan", "assets/scan-Bk6ZGfA8.js"),
        ("/register", "assets/register-CNMjCjPX.js"),
    ],
)
def test_each_page_loads_its_entry(pages: TestClient, path: str, script: str) -> None:
    response = pages.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"
    assert f'<script type="module" src="/static/{script}">' in response.text
    assert '<link rel="stylesheet" href="/static/assets/shared-qHQdyY8N.css">' in (
        response.text
    )
    assert '<div id="app"></div>' in response.text


@pytest.mark.parametrize("path", PAGES)
def test_volunteers_pages_have_the_band_and_nothing_of_an_administrators(
    pages: TestClient, path: str
) -> None:
    page = pages.get(path).text
    assert f'<a class="brand" href="{SCAN}">' in page
    assert "Sign out" not in page
    assert 'aria-label="Sections"' not in page


@pytest.mark.parametrize(
    ("dev_server", "source"), [(None, "build"), ("http://localhost:5173", "dev_server")]
)
def test_the_log_says_where_the_pages_scripts_come_from(
    make_app_without_database: Callable[..., FastAPI],
    tmp_path: Path,
    dev_server: str | None,
    source: str,
) -> None:
    with structlog.testing.capture_logs() as logs:
        make_app_without_database(
            frontend=FrontendConfig(dist=tmp_path, dev_server=dev_server),
            development=True,
        )
    (line,) = [line for line in logs if line["event"] == "frontend"]
    assert line["source"] == source


def test_the_log_names_the_build(
    make_app_without_database: Callable[..., FastAPI], dist: Path
) -> None:
    with structlog.testing.capture_logs() as logs:
        make_app_without_database(frontend=FrontendConfig(dist=dist))
    (line,) = [line for line in logs if line["event"] == "frontend"]
    assert line["build"] == BUILD_ID


def test_without_a_build_the_pages_say_so(
    make_app_without_database: Callable[..., FastAPI],
) -> None:
    with structlog.testing.capture_logs() as logs:
        app = make_app_without_database()
    assert [line["event"] for line in logs if line["log_level"] == "warning"] == [
        "frontend not built"
    ]
    with TestClient(app) as client:
        assert_not_built(client.get("/scan"))


def test_static_files_are_served_compressed_and_cached_for_good(
    pages: TestClient,
) -> None:
    response = pages.get(
        "/static/assets/decoder-9e1f3a2b.wasm", headers={"Accept-Encoding": "br, gzip"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/wasm"
    assert response.headers["content-encoding"] == "br"
    assert response.headers["vary"] == "Accept-Encoding"
    assert "immutable" in response.headers["cache-control"]
    assert "access-control-allow-origin" not in response.headers
    assert response.content == WASM  # Decoded by the client, as a browser does.


@pytest.mark.parametrize(
    ("accepted", "encoding"), [("gzip", "gzip"), ("identity", None)]
)
def test_a_browser_without_brotli_gets_gzip_or_the_plain_file(
    pages: TestClient, accepted: str, encoding: str | None
) -> None:
    response = pages.get(
        "/static/assets/scan-Bk6ZGfA8.js", headers={"Accept-Encoding": accepted}
    )
    assert response.headers.get("content-encoding") == encoding
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.content == SCRIPT


def test_the_pages_answer_head_too(pages: TestClient) -> None:
    response = pages.head("/scan")
    assert response.status_code == 200
    assert response.content == b""


@pytest.mark.parametrize(
    "path", ["/static/.vite/manifest.json", "/static/assets/scan-old.js"]
)
def test_the_manifest_and_missing_files_are_404s(pages: TestClient, path: str) -> None:
    assert pages.get(path).status_code == 404


def test_a_dist_that_is_not_a_directory_is_no_build(
    make_app_without_database: Callable[..., FastAPI], tmp_path: Path
) -> None:
    dist = tmp_path / "dist"
    dist.write_text("not a directory")
    with TestClient(
        make_app_without_database(frontend=FrontendConfig(dist=dist))
    ) as client:
        assert_not_built(client.get("/scan"))


@pytest.mark.parametrize(
    "manifest",
    [
        '{"src/scan.tsx": {"file": "a.js"',
        json.dumps({"src/register.tsx": {}}),
        "[]",
        json.dumps({"src/scan.tsx": [], "src/register.tsx": []}),
    ],
    ids=["half-written", "older-build", "not-vite", "entries-not-objects"],
)
def test_an_unusable_manifest_is_no_build(
    make_app_without_database: Callable[..., FastAPI], tmp_path: Path, manifest: str
) -> None:
    (tmp_path / MANIFEST).parent.mkdir(parents=True)
    (tmp_path / MANIFEST).write_text(manifest)
    with structlog.testing.capture_logs() as logs:
        app = make_app_without_database(frontend=FrontendConfig(dist=tmp_path))
    # It says what is wrong, not that there is no build.
    assert [line["event"] for line in logs if line["log_level"] == "warning"] == [
        "frontend manifest unusable"
    ]
    with TestClient(app) as client:
        assert_not_built(client.get("/scan"))


def test_development_reads_the_manifest_again_only_when_it_changes(
    make_app_without_database: Callable[..., FastAPI], tmp_path: Path
) -> None:
    """Every page loads the shared stylesheet: an unusable manifest says so
    once, not on every page."""
    (tmp_path / MANIFEST).parent.mkdir(parents=True)
    (tmp_path / MANIFEST).write_text("{}")
    with structlog.testing.capture_logs() as logs:
        app = make_app_without_database(
            frontend=FrontendConfig(dist=tmp_path), development=True
        )
        with TestClient(app) as client:
            for _ in range(3):
                client.get("/admin/sign-in")
    assert [line["event"] for line in logs if line["log_level"] == "warning"] == [
        "frontend manifest unusable"
    ]


def test_outside_development_the_build_is_the_one_at_startup(
    make_app_without_database: Callable[..., FastAPI], tmp_path: Path
) -> None:
    with TestClient(
        make_app_without_database(frontend=FrontendConfig(dist=tmp_path))
    ) as client:
        build(tmp_path)
        assert_not_built(client.get("/scan"))
        assert client.get("/static/assets/scan-Bk6ZGfA8.js").status_code == 404


def test_development_serves_each_new_build(
    make_app_without_database: Callable[..., FastAPI], tmp_path: Path
) -> None:
    app = make_app_without_database(
        frontend=FrontendConfig(dist=tmp_path), development=True
    )
    with TestClient(app) as client:
        assert_not_built(client.get("/scan"))
        build(tmp_path)
        assert client.get("/scan").status_code == 200
        assert client.get("/static/assets/scan-Bk6ZGfA8.js").status_code == 200
        # A rebuild names new files; the page follows it.
        rebuilt = json.loads((tmp_path / MANIFEST).read_text())
        rebuilt["src/scan.tsx"]["file"] = "assets/scan-N3wHa5h0.js"
        (tmp_path / BUILD_FILE).write_text(
            json.dumps({"build": "2026-11-13T00:00:00.000Z"})
        )
        (tmp_path / MANIFEST).write_text(json.dumps(rebuilt))
        assert "/static/assets/scan-N3wHa5h0.js" in client.get("/scan").text
        assert app.state.bundle.build_id() == "2026-11-13T00:00:00.000Z"
