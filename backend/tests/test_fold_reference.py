"""models.fold spells the sample in tests/fold_reference.tsv as it did
when the file was written, so a release of anyascii, or a Python with a
newer Unicode, that spells something otherwise fails here, not in
searches that stop matching rows folded the old way
(docs/decisions/0021, 2026-11-12)."""

from pathlib import Path

import pytest

from inventory_tng.models import FOLDED_WITH

from helpers import fold_reference
from helpers.fold_reference import COMMANDS, REFERENCE, characters, difference, main

pytestmark = pytest.mark.clone


def test_fold_spells_the_sample_as_the_reference_does() -> None:
    found = difference()
    assert found == "", (
        f"fold() no longer gives {REFERENCE.name}. If more than its first line "
        "(the versions of anyascii and of Python's Unicode) changed, decide "
        "whether to take it; then write it again "
        f"({COMMANDS}), and if spellings changed, fold again in a migration the "
        f"rows whose folded_with is not {FOLDED_WITH!r}:\n{found}"
    )


def test_the_sample_is_every_character_of_its_blocks_and_some_strings() -> None:
    sample = characters()
    assert {"é", "Ł", "ß", "Ω", "Ж", "€", "™", "\u2028"} <= set(sample)
    assert not {"\x85", "\u0378"} & set(sample)  # a control, unassigned
    lines = REFERENCE.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 + len(sample) + len(fold_reference.STRINGS)
    assert "Ł\tU+0141\tL" in lines
    # What would break a line, escaped: a line separator, to a space.
    assert "\\u2028\tU+2028\t\\x20" in lines


def test_check_writes_nothing_and_fails_while_stale(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stale = tmp_path / "fold_reference.tsv"
    stale.write_text("# old\n", encoding="utf-8")
    monkeypatch.setattr(fold_reference, "REFERENCE", stale)
    assert main(["--check"]) == 1
    assert "-# old" in capsys.readouterr().out
    assert stale.read_text(encoding="utf-8") == "# old\n"
    assert main([]) == 0
    assert main(["--check"]) == 0
    assert "is up to date" in capsys.readouterr().out
