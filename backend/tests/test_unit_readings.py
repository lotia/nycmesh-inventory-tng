"""The import's readings file (unit_readings.py; docs/decisions/0021, Q5-A,
inventory-tng-c0c.5): a dry run proposes how each unit after a number in
the names is read, the operator corrects the file once, later dry runs
keep the corrections, and loading it writes the first reading rules."""

import io
import runpy
import sys
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng import models, reading_rules, unit_readings, units
from inventory_tng.models import Category, Item, ReadingRule, unit_key

from helpers.rows import add, history
from helpers.sign_in import new_admin

NAMES = [
    "Cable, Cat6, 305M spool",
    "Tape 3M",
    "Box 10g",
    "Box 40 g",
    "Wire 16P",
]


def rows_of(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as text:
        return {row["unit"]: row for row in unit_readings.read(text)}


def corrected(path: Path, respell: str = "", **corrections: str) -> None:
    """The file at `path` with `corrections` by unit, and `respell` (the
    units, by spaces) said yes."""
    rows = rows_of(path)
    for unit, reading in corrections.items():
        rows[unit]["correction"] = reading
    for unit in respell.split():
        rows[unit]["respell"] = unit_readings.YES
    with path.open("w", encoding="utf-8", newline="") as text:
        unit_readings.write(text, rows.values())


def run_load(session: Session, path: Path) -> int:
    return unit_readings.run_load(session, unit_readings.parse(["load", str(path)]))


def rule_of(session: Session, token: str) -> ReadingRule:
    return session.scalars(select(ReadingRule).where(ReadingRule.token == token)).one()


def test_a_dry_run_proposes_as_the_seeding_page_does(tmp_path: Path) -> None:
    """A row per unit the seeding page would propose, from the same
    function, with its evidence; none for a unit nothing reads (16P)."""
    path = tmp_path / "readings.tsv"
    with capture_logs() as logs:
        done = unit_readings.propose(NAMES, path)
    assert done == unit_readings.Proposed(
        rows=2, corrected=0, to_choose=1, not_proposed=0
    )
    proposals = reading_rules.propose(NAMES, {})
    keys = ["M", "g"]
    assert sorted(rows_of(path)) == sorted(p.found.key for p in proposals) == keys
    rows = rows_of(path)
    assert rows["M"] == {
        "unit": "M",
        "proposed": "m",
        "correction": "",
        "respell": "",
        "offered": "Mbit/s (data rate) | m (meters) | none (not a unit)",
        "why": "elsewhere near cable, cat6, spool",
        "names": "1",
        "near": "cable 1 | cat6 1 | spool 1",
        "examples": "Tape 3M",
    }
    assert rows["g"]["proposed"] == ""
    assert rows["g"]["why"] == "nothing settles it: choose"
    assert rows["g"]["examples"] == "Box 10g | Box 40 g"
    text = path.read_text(encoding="utf-8")
    assert text.startswith(unit_readings.HEADER)
    (line,) = [line for line in logs if line["event"] == "unit readings proposed"]
    said = (line["units"], line["corrected"], line["to_choose"], line["not_proposed"])
    assert said == (2, 0, 1, 0)


def test_the_round_trip_dry_run_correct_dry_run_again_load(
    session: Session, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Corrected once, a later dry run keeps the corrections and rows no
    longer found; loading writes them as the import's rules, and names
    are read so."""
    path = tmp_path / "readings.tsv"
    unit_readings.propose(NAMES, path)
    corrected(path, g="Gbit/s", M="none")
    done = unit_readings.propose([*NAMES[2:], "Strip 2M"], path)
    assert done == unit_readings.Proposed(
        rows=2, corrected=2, to_choose=0, not_proposed=0
    )
    rows = rows_of(path)
    assert (rows["g"]["correction"], rows["M"]["correction"]) == ("Gbit/s", "none")
    assert rows["M"]["examples"] == "Strip 2M"
    capsys.readouterr()
    with capture_logs() as logs:
        assert run_load(session, path) == 0
    g, m = rule_of(session, "g"), rule_of(session, "M")
    assert (g.reading, g.decided_by_admin_id) == ("Gbit/s", None)
    assert (m.reading, m.decided_by_admin_id) == (None, None)
    assert history(session, "reading_rule")[-1].actor == "system:cli"
    assert capsys.readouterr().out.splitlines() == [
        "M: none",
        "g: Gbit/s",
        "Loaded 2 rules; 0 already so; 0 kept as an administrator set them.",
    ]
    (line,) = [line for line in logs if line["event"] == "unit readings loaded"]
    assert sorted(line["reading_rule_ids"]) == sorted([g.id, m.id])
    rules = models.reading_rules(session)
    assert unit_key("Box 10g", rules) == unit_key("Box 10 Gbps")
    (found,) = units.found_in("Tape 3M", rules)
    assert found.read is None
    # Loaded again: nothing to change.
    assert run_load(session, path) == 0
    assert capsys.readouterr().out.splitlines()[-1] == (
        "Loaded 0 rules; 2 already so; 0 kept as an administrator set them."
    )


def test_a_rule_an_administrator_set_is_kept_and_said(
    session: Session, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    admin, _ = new_admin(session, totp=False)
    reading_rules.decide(session, {"g": units.Rule("g")}, admin.id)
    path = tmp_path / "readings.tsv"
    unit_readings.propose(NAMES, path)
    corrected(path, g="Gbit/s")
    assert run_load(session, path) == 0
    assert rule_of(session, "g").reading == "g"
    assert capsys.readouterr().out.splitlines() == [
        "M: m",
        "g: kept g, set by an administrator; the file says Gbit/s",
        "Loaded 1 rules; 0 already so; 1 kept as an administrator set them.",
    ]


@pytest.mark.parametrize(
    ("corrections", "said"),
    [
        ({}, "g: no reading; write one in its correction"),
        ({"g": "kg"}, "g: kg is not one of its readings: Gbit/s g none"),
    ],
)
def test_a_file_with_a_unit_unread_loads_nothing(
    session: Session,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    corrections: dict[str, str],
    said: str,
) -> None:
    path = tmp_path / "readings.tsv"
    unit_readings.propose(NAMES, path)
    corrected(path, **corrections)
    assert run_load(session, path) == 1
    assert capsys.readouterr().err.splitlines() == [f"{path}: nothing loaded.", said]
    assert session.scalars(select(ReadingRule)).all() == []


def test_rows_twice_without_a_unit_or_badly_respelt_are_refused() -> None:
    blank = dict.fromkeys(unit_readings.COLUMNS, "")
    rows = [
        {**blank, "unit": "g", "proposed": "g", "respell": " Yes "},
        {**blank, "unit": "g", "proposed": "g"},
        blank,
        {**blank, "unit": "M", "proposed": "m", "respell": "no"},
        {**blank, "unit": "G", "correction": "none", "respell": "yes"},
    ]
    assert unit_readings.chosen(rows) == (
        {"g": units.Rule("g", respell=True)},
        [
            "g: more than one row",
            "a row has no unit",
            "M: respell is yes or empty, not no",
            "G: none is never respelt; empty its respell",
        ],
    )


def test_a_row_no_longer_proposed_is_kept_and_said(tmp_path: Path) -> None:
    """A later dry run for other names keeps a row it no longer proposes,
    with its correction and respell, as loading still reads names by it."""
    path = tmp_path / "readings.tsv"
    unit_readings.propose(NAMES, path)
    corrected(path, respell="M", M="m")
    done = unit_readings.propose(["Box 10g"], path)
    assert done == unit_readings.Proposed(
        rows=2, corrected=1, to_choose=1, not_proposed=1
    )
    m = rows_of(path)["M"]
    assert (m["correction"], m["respell"]) == ("m", "yes")
    assert (m["why"], m["names"]) == (unit_readings.NOT_PROPOSED, "0")


def test_propose_refuses_a_file_with_a_unit_twice(tmp_path: Path) -> None:
    path = tmp_path / "readings.tsv"
    unit_readings.propose(NAMES, path)
    with path.open("a", encoding="utf-8") as text:
        text.write("g\t\tg\n")
    with pytest.raises(ValueError, match="more than one row"):
        unit_readings.propose(NAMES, path)


def test_a_respelt_rule_loads_and_a_reload_says_what_it_was(
    session: Session, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "readings.tsv"
    unit_readings.propose(NAMES, path)
    corrected(path, respell="M", g="Gbit/s")
    assert run_load(session, path) == 0
    assert rule_of(session, "M").rule == units.Rule("m", respell=True)
    assert capsys.readouterr().out.splitlines()[:2] == ["M: m, respelt", "g: Gbit/s"]
    corrected(path, g="g")
    assert run_load(session, path) == 0
    assert capsys.readouterr().out.splitlines() == [
        "g: g (was Gbit/s, by an earlier load)",
        "Loaded 1 rules; 1 already so; 0 kept as an administrator set them.",
    ]


def test_a_file_a_spreadsheet_saved_is_read(
    session: Session, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A byte order mark, comment and empty rows quoted, and rows short of
    their trailing empty cells are read as the file says."""
    path = tmp_path / "readings.tsv"
    columns = "\t".join(unit_readings.COLUMNS)
    path.write_text(
        f'\ufeff"# a comment"\t\n\t\t\n{columns}\ng\t\tGbit/s\nM\tm\n',
        encoding="utf-8",
    )
    assert run_load(session, path) == 0
    assert sorted(capsys.readouterr().out.splitlines()[:2]) == ["M: m", "g: Gbit/s"]


def test_a_file_not_of_these_columns_is_refused(
    session: Session, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "readings.tsv"
    path.write_text("unit\treading\ng\tg\n", encoding="utf-8")
    assert run_load(session, path) == 1
    args = unit_readings.parse(["propose", str(path), "--file", str(path)])
    assert unit_readings.run_propose(args) == 1
    err = capsys.readouterr().err.splitlines()
    assert (
        err == [f"{path}: its columns must be: {' '.join(unit_readings.COLUMNS)}"] * 2
    )


def test_a_load_that_would_make_two_names_one_is_refused(
    session: Session, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Loaded into a database with names (not an import's), names read
    alike by the rules refuse it, named by id."""
    category = add(session, Category(name="Boxes"))
    grams = add(session, Item(name="Box 10g", category_id=category.id))
    rate = add(session, Item(name="Box 10 Gbps", category_id=category.id))
    path = tmp_path / "readings.tsv"
    unit_readings.propose(NAMES, path)
    corrected(path, g="Gbit/s")
    assert run_load(session, path) == 1
    assert capsys.readouterr().err.splitlines() == [
        "Nothing loaded: these names would read alike; rename one of each first.",
        f"item {grams.id} (Box 10g) and item {rate.id} (Box 10 Gbps)",
    ]
    assert session.scalars(select(ReadingRule)).all() == []


@pytest.mark.usefixtures("restore_logging")
def test_the_commands_run_as_operator_commands(
    own_database: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """As DEPLOYMENT.md runs them: names and the file from standard input."""
    monkeypatch.setenv("DATABASE_URL", own_database)
    path = tmp_path / "readings.tsv"
    stdin = io.TextIOWrapper(io.BytesIO("\n".join(NAMES).encode()))
    monkeypatch.setattr(sys, "stdin", stdin)
    assert unit_readings.main(["propose", "-", "--file", str(path)]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == (
        f"Wrote {path}: 2 units, 0 corrected, 1 to choose, 0 no longer proposed."
    )
    corrected(path, g="Gbit/s")
    stdin = io.TextIOWrapper(io.BytesIO(path.read_bytes()))
    monkeypatch.setattr(sys, "stdin", stdin)
    assert unit_readings.main(["load", "-"]) == 0
    assert capsys.readouterr().out.splitlines()[-1].startswith("Loaded 2 rules")


def test_propose_refuses_outside_its_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(unit_readings, "set_up", lambda: None)
    assert unit_readings.main(["propose", "-", "--file", str(tmp_path / "r")]) == 1


def test_unit_readings_runs_as_a_module(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["unit_readings", "--help"])
    with (
        pytest.warns(RuntimeWarning, match="found in sys.modules"),
        pytest.raises(SystemExit) as done,
    ):
        runpy.run_module("inventory_tng.unit_readings", run_name="__main__")
    assert done.value.code == 0
    assert "propose" in capsys.readouterr().out


def test_names_not_in_utf_8_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names = tmp_path / "names.txt"
    names.write_bytes("Câble 3M\n".encode("latin-1"))
    args = unit_readings.parse(["propose", str(names), "--file", str(tmp_path / "r")])
    assert unit_readings.run_propose(args) == 1
    assert capsys.readouterr().err.startswith(f"{names}: 'utf-8' codec")


def test_names_from_a_standard_input_already_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Standard input that is not a file's (a test's, an embedding's) is read
    as it is."""
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(NAMES)))
    path = tmp_path / "readings.tsv"
    assert (
        unit_readings.run_propose(
            unit_readings.parse(["propose", "-", "--file", str(path)])
        )
        == 0
    )
    assert sorted(rows_of(path)) == ["M", "g"]
