"""The unit table, and searches reading units after a number
(inventory-tng-c0c.2, docs/decisions/0021, 2026-11-14)."""

import json
import re
import runpy
import sys
import unicodedata
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng import items, units
from inventory_tng.admin.items import ITEMS
from inventory_tng.models import Category, Item, fold
from inventory_tng.units import KINDS, PREFIXES, UNITS, Reading, read, respell

from helpers.rows import add, tag
from helpers.sign_in import text as page_text

UCUM = Path(__file__).parent / "ucum_codes.tsv"
VECTORS = units.TABLE.with_name("units-vectors.json")
MIGRATIONS = Path(__file__).parent.parent / "migrations" / "versions"
# The SI's newest prefixes (2022), which UCUM 2.2 has not.
NEWER_THAN_UCUM = {"Q", "R", "r", "q"}


def _ucum() -> dict[str, dict[str, str]]:
    """UCUM's codes, by kind (prefix or unit): each one's last field."""
    found: dict[str, dict[str, str]] = {"prefix": {}, "unit": {}}
    for line in UCUM.read_text(encoding="utf-8").splitlines():
        if not line.startswith("#"):
            kind, code, _name, value = line.split("\t")
            found[kind][code] = value
    return found


def _derived() -> list[tuple[str, str]]:
    """Every spelling the table reads, each with its symbol, prefixed ones
    included."""
    spelt: list[tuple[str, str]] = []
    for unit in UNITS:
        for spelling in (*unit.prefixed(), *unit.words):
            spelt.append((spelling, unit.symbol))
        if unit.prefixes:
            spelt += [
                (each + spelling, prefix.symbol + unit.symbol)
                for prefix in PREFIXES
                for each in (prefix.symbol, *prefix.spellings)
                for spelling in unit.prefixed()
            ]
    return spelt


def test_every_ucum_code_is_ucums_and_prefixes_go_only_on_metric_units() -> None:
    ucum = _ucum()
    used: set[str] = set()
    for unit in UNITS:
        for code in unit.ucum:
            for i, part in enumerate(code.split("/")):
                atom = part.rstrip("0123456789")
                assert atom in ucum["unit"], (unit.symbol, code)
                used.add(atom)
                if unit.prefixes and i == 0:
                    assert ucum["unit"][atom] == "yes", (unit.symbol, code)
        if unit.factor is not None:
            # A unit UCUM has carries its code; only levels in dB have none.
            assert unit.ucum, unit.symbol
    assert used == set(ucum["unit"]), "a UCUM code listed but not used"
    table = {prefix.symbol: prefix.exponent for prefix in PREFIXES}
    assert {code: int(exponent) for code, exponent in ucum["prefix"].items()} == {
        code: e for code, e in table.items() if code not in NEWER_THAN_UCUM
    }


def test_each_unit_is_one_row_of_a_kind_with_a_plausible_size() -> None:
    assert len({unit.symbol for unit in UNITS}) == len(UNITS)
    assert {unit.kind for unit in UNITS} == set(KINDS)
    for kind in KINDS.values():
        low, high = kind.plausible
        assert low < high
        base = next(unit for unit in UNITS if unit.symbol == kind.base)
        assert (base.factor in {1.0, None}, base.offset) == (True, 0.0)
    for unit in UNITS:
        assert unit.symbol.isascii(), unit.symbol


def test_no_two_units_share_a_spelling_in_any_case() -> None:
    whole: dict[str, str] = {}
    for unit in UNITS:
        for spelling in {s.lower() for s in (*unit.prefixed(), *unit.words)}:
            assert whole.setdefault(spelling, unit.symbol) == unit.symbol, spelling


def test_every_spelling_reads_as_its_unit_and_in_any_case_as_one() -> None:
    for spelling, symbol in _derived():
        assert str(read(spelling)) == symbol, spelling
        for typed in (spelling.lower(), spelling.upper()):
            reading = read(typed)
            assert reading is not None, typed
            assert str(reading).lower() == symbol.lower(), typed
        # The symbol is a spelling of its own: folding twice changes nothing.
        assert str(read(symbol)) == symbol


def test_a_unit_is_read_with_its_prefix_as_typed_if_it_can_be() -> None:
    assert read("MHz") == Reading(PREFIXES[8], UNITS[5])
    assert (str(read("MHZ")), str(read("mhz"))) == ("MHz", "mHz")
    assert str(read("Kbps")) == "kbit/s"
    assert str(read("DAM")) == "dam"
    assert read("Cell") is None
    assert read("16P") is None


@pytest.mark.clone
def test_the_scan_pages_table_is_current() -> None:
    """The scan page reads units.json; a change to what reads a unit raises
    VERSION, as rows folded before are folded again."""
    committed = json.loads(units.TABLE.read_text(encoding="utf-8"))
    if committed != units.table():
        assert committed["version"] != units.VERSION, (
            "The unit table changed: raise units.VERSION, fold the rows again "
            "in a migration, then write units.json again"
        )
        pytest.fail("Run `uv run python -m inventory_tng.units` from backend/")
    # Laid out as Biome lays it out, so its check passes.
    assert units.TABLE.read_text(encoding="utf-8") == units.laid_out(committed) + "\n"


def test_the_newest_unit_table_has_a_migration_folding_again() -> None:
    """Rows folded with an older table are folded again on deploy, by a
    migration such as 0036 naming units.VERSION in FOLDS_UNITS."""
    named = [
        int(found[1])
        for migration in MIGRATIONS.glob("*.py")
        if (
            found := re.search(
                r"^FOLDS_UNITS = (\d+)$",
                migration.read_text(encoding="utf-8"),
                re.MULTILINE,
            )
        )
    ]
    assert max(named) == units.VERSION, (
        "Add a migration folding again the rows folded with an older table"
    )


def test_writes_the_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # As the task runs it, here to another file.
    monkeypatch.setattr(sys, "argv", ["units", str(tmp_path / "units.json")])
    with pytest.warns(RuntimeWarning, match="found in sys.modules"):
        runpy.run_module("inventory_tng.units", run_name="__main__")
    written = (tmp_path / "units.json").read_text(encoding="utf-8")
    assert json.loads(written) == units.table()


@pytest.mark.clone
@pytest.mark.parametrize(
    "vector",
    json.loads(VECTORS.read_text(encoding="utf-8"))["vectors"]
    if VECTORS.exists()
    else [],
    ids=lambda vector: vector["text"],
)
def test_the_server_and_the_scan_page_read_units_alike(vector: dict[str, str]) -> None:
    """units-vectors.json, which the scan page's tests read too."""
    text = vector["text"]
    assert respell(unicodedata.normalize("NFKD", text)) == vector["respelt"]
    if "searched" in vector:
        assert fold(text).lower() == vector["searched"]


def test_units_are_read_only_after_a_number_and_greek_elsewhere_is_letters() -> None:
    assert fold("10uF 10 µF 10μF 10 ΜF") == "10uF 10uF 10uF 10uF"  # noqa: RUF001  Greek capitals, on purpose.
    assert fold("85 degC 85°C 85 Cel 85℃ 85 deg C") == " ".join(["85degC"] * 5)
    assert fold("4.7 kΩ 4.7kohm") == "4.7kOhm 4.7kOhm"
    # Alone, a unit is read only if spelt with a sign no word is: so "μF"
    # finds "10μF", but "Cel" is not read, nor μ, a Greek letter alone.
    assert fold("μF Ω °C ℃ Cel uF μ") == "uF Ohm degC degC Cel uF m"
    assert fold("μέρα ΜΕΡΑ ωμέγα 10 μέρα") == "mera MERA omega 10 mera"  # noqa: RUF001  Greek capitals, on purpose.
    # Neither is a word that only starts as one does.
    assert fold("10 Cell 10 mé 10 m/s") == "10 Cell 10 me 10 m/s"


def test_each_field_is_read_alone() -> None:
    """A field's number never reads the next field's start as a unit."""
    item = Item(name="Spool 305", manufacturer="M Corp", part_number="10 m")
    item.refold()
    assert item.search_text == "Spool 305 M Corp 10m "
    assert item.name_folded == "Spool 305"


def test_every_spelling_of_a_unit_finds_the_others(
    admin_client: TestClient, session: Session
) -> None:
    """'10uF' finds '10μF' (inventory-tng-8g2), and so does 'μF' alone, as
    before units were read; a capital Greek Mu is read as micro after a
    number as the scan page reads it, and Greek words are found as letters
    (inventory-tng-5d2)."""
    parts = add(session, Category(name=f"Parts {tag()}"))
    for name, typed in (
        ("Cap 10μF", ("10uF", "10 µF", "10μF", "10 ΜF", "10 UF", "μF", "µF")),  # noqa: RUF001  Greek capitals, on purpose.
        ("Sensor 85 °C", ("85 degC", "85°C", "85 Cel", "85℃", "85 deg C", "°C", "℃")),
        ("Resistor 4.7 kΩ", ("4.7kohm", "4.7 kΩ", "4.7kOhm", "kΩ")),
        ("Radio 5 GHz", ("5ghz", "5 GHz")),
        ("ΜΕΡΑ cable", ("μέρα", "mera")),  # noqa: RUF001  Greek capitals, on purpose.
    ):
        item = add(session, Item(name=f"{name} {tag()}", category_id=parts.id))
        for each in typed:
            listed = page_text(admin_client.get(ITEMS, params={"q": each}))
            assert item.name in listed, each
    assert any(
        found.name.startswith("Cap 10μF")
        for found in items.similar(session, "Cap 10 uF", 0)
    )


def test_a_unit_ends_where_a_letter_or_digit_of_any_script_does_not_follow() -> None:
    """units._ENDS's [^\\W_] is the scan page's [\\p{L}\\p{N}]: a letter or
    digit in Unicode's sense (category L or N), for every character."""
    ends = re.compile(units._ENDS)
    differ = [
        hex(point)
        for point in range(sys.maxunicode + 1)
        if (ends.match(chr(point)) is None)
        != (unicodedata.category(chr(point))[0] in "LN")
    ]
    assert differ == []
