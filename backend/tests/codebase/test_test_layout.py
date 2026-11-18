"""The tests mirror the source tree: every folder under backend/tests/
names a package of the app or devtools/, or holds a README.md saying what
it holds (CONTRIBUTING.md, Where tests go)."""

from pathlib import Path

import devtools
import inventory_tng

TESTS = Path(__file__).parents[1]
SOURCE = Path(inventory_tng.__file__).parent
PACKAGES = {
    path.parent.relative_to(SOURCE) for path in SOURCE.glob("**/*/__init__.py")
} | {Path(Path(devtools.__file__).parent.name)}


def holds_files(folder: Path) -> bool:
    """False for a folder left holding only bytecode, as one is after its
    tests move away, since the version control ignores __pycache__."""
    return any(
        path.is_file() for path in folder.rglob("*") if "__pycache__" not in path.parts
    )


def test_every_folder_mirrors_a_package_or_says_what_it_holds() -> None:
    unexplained = sorted(
        str(relative)
        for folder in TESTS.rglob("*")
        if folder.is_dir()
        and "__pycache__" not in folder.parts
        and (relative := folder.relative_to(TESTS)) not in PACKAGES
        and not (folder / "README.md").is_file()
        and holds_files(folder)
    )
    assert unexplained == []
