import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from servestatic.compress import Compressor

from inventory_tng.bundle import MANIFEST, STYLES, Bundle, Tags
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
}
SCRIPT = b"export const answer = 42;\n" * 40
WASM = b"\0asm\1\0\0\0" + b"\0" * 2000


def build(dist: Path) -> None:
    """A build as the container makes it: Vite's files, each beside its
    Brotli and gzip copies, from the compressor the container runs."""
    (dist / MANIFEST).parent.mkdir(parents=True)
    (dist / MANIFEST).write_text(json.dumps(BUILD))
    (dist / "assets").mkdir()
    for name, data in [("scan-Bk6ZGfA8.js", SCRIPT), ("decoder-9e1f3a2b.wasm", WASM)]:
        (dist / "assets" / name).write_bytes(data)
        Compressor(use_zstd=False, quiet=True).compress(dist / "assets" / name)


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    build(tmp_path)
    return tmp_path


@pytest.fixture
def pages(make_app: Callable[..., FastAPI], dist: Path) -> Iterator[TestClient]:
    with TestClient(make_app(frontend=FrontendConfig(dist=dist))) as client:
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


def test_the_shared_stylesheet_is_its_own_entry(dist: Path) -> None:
    bundle = Bundle(FrontendConfig(dist=dist), development=False)
    assert bundle.tags(STYLES) == Tags(styles=("/static/assets/styles-BXvLJlQA.css",))


def test_server_rendered_pages_load_the_shared_stylesheet(
    pages: TestClient, make_app: Callable[..., FastAPI]
) -> None:
    link = '<link rel="stylesheet" href="/static/assets/styles-BXvLJlQA.css">'
    assert link in pages.get("/admin/sign-in").text
    # Without a build, a plain page.
    with TestClient(make_app()) as unbuilt:
        assert "stylesheet" not in unbuilt.get("/admin/sign-in").text


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


@pytest.mark.parametrize(
    ("dev_server", "source"), [(None, "build"), ("http://localhost:5173", "dev_server")]
)
def test_the_log_says_where_the_pages_scripts_come_from(
    make_app: Callable[..., FastAPI],
    tmp_path: Path,
    dev_server: str | None,
    source: str,
) -> None:
    with structlog.testing.capture_logs() as logs:
        make_app(
            frontend=FrontendConfig(dist=tmp_path, dev_server=dev_server),
            development=True,
        )
    (line,) = [line for line in logs if line["event"] == "frontend"]
    assert line["source"] == source


def test_without_a_build_the_pages_say_so(make_app: Callable[..., FastAPI]) -> None:
    with structlog.testing.capture_logs() as logs:
        app = make_app()
    assert [line["event"] for line in logs if line["log_level"] == "warning"] == [
        "frontend not built"
    ]
    with TestClient(app) as client:
        assert client.get("/scan").status_code == 503


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
    make_app: Callable[..., FastAPI], tmp_path: Path
) -> None:
    dist = tmp_path / "dist"
    dist.write_text("not a directory")
    with TestClient(make_app(frontend=FrontendConfig(dist=dist))) as client:
        assert client.get("/scan").status_code == 503


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
    make_app: Callable[..., FastAPI], tmp_path: Path, manifest: str
) -> None:
    (tmp_path / MANIFEST).parent.mkdir(parents=True)
    (tmp_path / MANIFEST).write_text(manifest)
    with structlog.testing.capture_logs() as logs:
        app = make_app(frontend=FrontendConfig(dist=tmp_path))
    # It says what is wrong, not that there is no build.
    assert [line["event"] for line in logs if line["log_level"] == "warning"] == [
        "frontend manifest unusable"
    ]
    with TestClient(app) as client:
        assert client.get("/scan").status_code == 503


def test_development_reads_the_manifest_again_only_when_it_changes(
    make_app: Callable[..., FastAPI], tmp_path: Path
) -> None:
    """Every page loads the shared stylesheet: an unusable manifest says so
    once, not on every page."""
    (tmp_path / MANIFEST).parent.mkdir(parents=True)
    (tmp_path / MANIFEST).write_text("{}")
    with structlog.testing.capture_logs() as logs:
        app = make_app(frontend=FrontendConfig(dist=tmp_path), development=True)
        with TestClient(app) as client:
            for _ in range(3):
                client.get("/admin/sign-in")
    assert [line["event"] for line in logs if line["log_level"] == "warning"] == [
        "frontend manifest unusable"
    ]


def test_outside_development_the_build_is_the_one_at_startup(
    make_app: Callable[..., FastAPI], tmp_path: Path
) -> None:
    with TestClient(make_app(frontend=FrontendConfig(dist=tmp_path))) as client:
        build(tmp_path)
        assert client.get("/scan").status_code == 503
        assert client.get("/static/assets/scan-Bk6ZGfA8.js").status_code == 404


def test_development_serves_each_new_build(
    make_app: Callable[..., FastAPI], tmp_path: Path
) -> None:
    app = make_app(frontend=FrontendConfig(dist=tmp_path), development=True)
    with TestClient(app) as client:
        assert client.get("/scan").status_code == 503
        build(tmp_path)
        assert client.get("/scan").status_code == 200
        assert client.get("/static/assets/scan-Bk6ZGfA8.js").status_code == 200
        # A rebuild names new files; the page follows it.
        rebuilt = json.loads((tmp_path / MANIFEST).read_text())
        rebuilt["src/scan.tsx"]["file"] = "assets/scan-N3wHa5h0.js"
        (tmp_path / MANIFEST).write_text(json.dumps(rebuilt))
        assert "/static/assets/scan-N3wHa5h0.js" in client.get("/scan").text
