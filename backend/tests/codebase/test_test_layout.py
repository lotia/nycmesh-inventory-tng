"""The tests mirror the source tree: every folder under backend/tests/
names a package of the app or devtools/, or holds a README.md saying what
it holds (CONTRIBUTING.md, Where tests go)."""

from pathlib import Path

from helpers.source import SOURCE

TESTS = Path(__file__).parents[1]
PACKAGES = {
    path.parent.relative_to(SOURCE) for path in SOURCE.glob("**/*/__init__.py")
} | {Path("devtools")}


def test_every_folder_mirrors_a_package_or_says_what_it_holds() -> None:
    # A folder holding only bytecode, as one is after its tests move away
    # (the version control ignores __pycache__), holds nothing of ours.
    folders = {
        folder
        for path in TESTS.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
        for folder in path.relative_to(TESTS).parents[:-1]
    }
    unexplained = sorted(
        str(folder)
        for folder in folders
        if folder not in PACKAGES and not (TESTS / folder / "README.md").is_file()
    )
    assert unexplained == []
