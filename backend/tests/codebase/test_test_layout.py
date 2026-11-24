"""The tests mirror the source tree: tests/inventory_tng/ mirrors the app's
package and tests/devtools/ the developer tools, each folder in them a
package of its own, and every other folder under backend/tests/ holds a
README.md saying what it holds (CONTRIBUTING.md, Where tests go)."""

from pathlib import Path

import devtools

from helpers.source import SOURCE

TESTS = Path(__file__).parents[1]
# Each folder under tests/ that mirrors a package, and that package.
MIRRORS = {
    Path("inventory_tng"): SOURCE,
    Path("devtools"): Path(devtools.__file__).parent,
}


def _folders(root: Path) -> set[Path]:
    """Each folder under `root` holding a file of ours, relative to it. A
    folder holding only bytecode, as one is after its tests move away (the
    version control ignores __pycache__), holds nothing of ours."""
    return {
        path.parent.relative_to(root)
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def _mirrored(folder: Path) -> bool:
    return any(folder.is_relative_to(mirror) for mirror in MIRRORS)


def test_every_mirrored_folder_is_a_package() -> None:
    unmatched = sorted(
        str(mirror / folder)
        for mirror, package in MIRRORS.items()
        for folder in _folders(TESTS / mirror)
        if not (package / folder / "__init__.py").is_file()
    )
    assert unmatched == []


def test_every_other_folder_says_what_it_holds() -> None:
    folders = {
        parent
        for folder in _folders(TESTS)
        for parent in (folder, *folder.parents[:-1])
    }
    unexplained = sorted(
        str(folder)
        for folder in folders
        if folder != Path()
        and not _mirrored(folder)
        and not (TESTS / folder / "README.md").is_file()
    )
    assert unexplained == []
