"""The unit table, and searches reading units after a number
(inventory-tng-c0c.2, docs/decisions/0021, 2026-11-14)."""

import json
import re
import runpy
import sys
import unicodedata
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng import items, units
from inventory_tng.admin.items import ITEMS
from inventory_tng.models import Category, Item, fold, folds, held, unit_key
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
        pytest.fail("Run `mise run regenerate-units`")


def test_the_newest_unit_table_has_a_migration_folding_again() -> None:
    """Rows folded with an older table are folded again on deploy, by a
    migration such as 0036 naming units.VERSION in FOLDS_UNITS."""
    named = re.findall(
        r"^FOLDS_UNITS = (\d+)$",
        "\n".join(m.read_text(encoding="utf-8") for m in MIGRATIONS.glob("*.py")),
        re.MULTILINE,
    )
    assert max(map(int, named)) == units.VERSION, (
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
    ids=lambda vector: " ".join(
        [vector["text"], *(f"{k}={v}" for k, v in vector.get("rules", {}).items())]
    ),
)
def test_the_server_and_the_scan_page_read_units_alike(vector: dict[str, Any]) -> None:
    """units-vectors.json, which the scan page's tests read too, its
    reading rules first where it has them."""
    text = vector["text"]
    rules = {k: units.Rule(v) for k, v in vector.get("rules", {}).items()}
    assert respell(unicodedata.normalize("NFKD", text), rules) == vector["respelt"]
    forms = [form.lower() for form in folds(text, rules)]
    if "searched" in vector:
        assert forms[-1] == vector["searched"]
    if "typed" in vector:
        assert forms[0] == vector["typed"]
        assert len(forms) == (1 if vector["typed"] == vector["searched"] else 2)


@pytest.mark.clone
@pytest.mark.parametrize(
    "vector",
    json.loads(VECTORS.read_text(encoding="utf-8"))["groups"]
    if VECTORS.exists()
    else [],
    ids=lambda vector: " ".join(
        [vector["text"], *(f"{k}={v}" for k, v in vector.get("rules", {}).items())]
    ),
)
def test_the_server_and_the_scan_page_group_a_search_alike(
    vector: dict[str, Any],
) -> None:
    """units-vectors.json's groups, which the scan page's tests read too
    (Ali, 2026-11-15, inventory-tng-ist.1)."""
    rules = {k: units.Rule(v) for k, v in vector.get("rules", {}).items()}
    decomposed = unicodedata.normalize("NFKD", vector["text"])
    assert units.groups(decomposed, rules) == [tuple(g) for g in vector["groups"]]


def test_search_reads_vdc_and_thousands_but_never_in_a_name() -> None:
    """Ali, 2026-11-15 (inventory-tng-ist.1): VDC and VAC are volts
    and `1,000 ft`, `1 000 ft` and `1000 ft` one, to search only; a name
    is never respelt so, nor its key read so."""
    assert fold("48 VDC 24 vac 1,000 ft 1\N{THIN SPACE}000 ft") == (
        "48V 24V 1000ft 1000ft"
    )
    assert units.readings("VDC") == []
    assert units.found_in("PSU 48 VDC") == []
    assert units.read_as("Spool 1,000 ft 48 VDC") == "Spool 1,000ft 48 VDC"
    rules = {"ft": units.Rule("ft", respell=True)}
    assert units.spell("Spool 1,000 ft", units.found_in("Spool 1,000 ft", rules)) == (
        "Spool 1,000ft"
    )


def test_search_reads_a_comma_after_a_lone_nought_as_a_decimal_point() -> None:
    """Ali, 2026-11-15 (inventory-tng-4i1): `0,500 m` is `0.500 m` to
    search, every other comma between digits groups thousands, and a name
    is read and shown as typed."""
    assert fold("0,500 m 0,5 m 10,500 m") == fold("0.500 m 0.5 m 10500 m")
    assert units.read_as("Cable 0,500 m") == "Cable 0,500m"
    assert units.shown_name("Cable 0,500 m") == "Cable 0,500\N{NO-BREAK SPACE}m"


@pytest.mark.parametrize(
    "typed", ["Scale 0,500 Mg", "Resistor 0,5 MOHM", "Probe -0,001 mK"]
)
def test_a_form_reads_a_comma_after_a_lone_nought_as_search_does(typed: str) -> None:
    """Ali, 2026-11-15 (inventory-tng-4i1): a form judges `0,500 Mg` as
    `0.500 Mg`, as search reads it, not as 500, and keeps it as typed."""
    (comma,) = units.found_in(typed)
    (point,) = units.found_in(typed.replace(",", "."))
    assert (comma.read, comma.plausible, comma.asks) == (
        point.read,
        point.plausible,
        point.asks,
    )
    assert comma.number == point.number.replace(".", ",")
    assert units.respell(typed) == units.respell(typed.replace(",", "."))


@pytest.mark.parametrize(
    ("typed", "number"),
    [
        ("Box 1 000 M", "1 000"),
        ("Box 1\u202f000 M", "1\u202f000"),
        ("Box 1,000 M", "1,000"),
        ("Box 12 345 m", "12 345"),
    ],
)
def test_a_form_reads_a_number_grouped_in_thousands_as_search_does(
    typed: str, number: str
) -> None:
    """Ali, 2026-11-16 (inventory-tng-t78, inventory-tng-uxo6): a form
    judges `1 000 M` as `1000 M`, as search reads it, not as 000, so asks
    about it, and keeps the number as typed."""
    (grouped,) = units.found_in(typed)
    joined = re.sub(r"[ ,\u202f]", "", number)
    (plain,) = units.found_in(typed.replace(number, joined))
    assert (grouped.read, grouped.plausible, grouped.asks) == (
        plain.read,
        plain.plausible,
        plain.asks,
    )
    assert grouped.number == number


def test_names_reading_alike_but_for_a_number_grouped_by_spaces_are_one() -> None:
    """Ali, 2026-11-16 (inventory-tng-t78): the read-alike check ignores
    spaces, and reads a number grouped by them as search does."""
    for spaced, joined in (
        ("Cap 1 000 μF", "Cap 1000 uF"),
        ("Res 1 000 Ω", "Res 1000 ohm"),
        ("Res 1\u202f000 Ω", "Res 1000 ohm"),
    ):
        assert unit_key(spaced) == unit_key(joined), spaced


@pytest.mark.parametrize("space", ["\u00a0", "\u202f", "\u2009", "\u1680"])
def test_a_unit_after_any_space_is_judged_as_search_reads_it(space: str) -> None:
    """inventory-tng-vw0f: search reads every space as a plain one, so the
    forms and the read-alike check find a unit after a number and a
    no-break, thin or other space as after a plain one, and say where it is
    in the name as typed."""
    typed = f"Box 1000{space}M"
    (found,) = units.found_in(typed)
    (plain,) = units.found_in("Box 1000 M")
    assert (found.read, found.plausible, found.asks) == (
        plain.read,
        plain.plausible,
        plain.asks,
    )
    assert (found.number, found.token, found.start, found.end) == (
        "1000",
        "M",
        8,
        len(typed),
    )
    assert unit_key(f"Cap 1000{space}\u03bcF") == unit_key("Cap 1000 uF")
    assert units.read_as(f"Cap 1000{space}\u03bcF") == "Cap 1000uF"
    assert units.respell(f"Cap 1000{space}\u03bcF") == "Cap 1000uF"


def test_a_product_before_any_space_is_not_grouped_with_the_next_number() -> None:
    """inventory-tng-uxo6: `Cat 6 305 m` is a product's number, then 305 m,
    whatever space is after `Cat`, to the forms and to search alike."""
    for space in ("\u00a0", "\u202f", "\u1680"):
        typed = f"Cat{space}6 305 m"
        assert [f.number for f in units.found_in(typed)] == ["305"], space
        assert units.respell(typed) == f"Cat{space}6 305m", space


def test_a_sign_belongs_to_the_number_grouped_after_it() -> None:
    """inventory-tng-uxo6: `-1 000` and `-1,000` are minus a thousand to the
    forms, to search and to the shown name alike, so a kind that may be at
    or below nought is judged by the sign: -1000 °C is no plausible
    temperature, 1000 °C is."""
    for typed in ("Kiln -1 000 Cel", "Kiln \u22121 000 Cel", "Kiln -1,000 Cel"):
        (found,) = units.found_in(typed)
        number = typed.removeprefix("Kiln ").removesuffix(" Cel")
        assert (found.number, found.plausible) == (number, ()), typed
        assert units.shown_name(typed) == typed
        (unsigned,) = units.found_in(typed.replace("-", "").replace("\u2212", ""))
        assert str(unsigned.read) == "degC"
    assert units.respell("Kiln -1 000 Cel") == "Kiln -1000degC"
    assert units.groups("-1 000 V") == [("-1 000 V", "-1000V")]


def test_numbers_spaced_as_thousands_are_one_but_each_still_found() -> None:
    """docs/decisions/0021, accepted misreading: `2 100 ft` is searched as
    `2100ft` too, kept as typed beside it, so `100 ft` and `100 feet` still
    find it."""
    kept = held("Pack 2 100 ft")
    assert kept == "Pack 2 100 ft Pack 2100ft"
    assert all(
        any(form.lower() in kept.lower() for form in folds(search))
        for search in ("100 ft", "100 feet", "100ft", "2 100 ft")
    )


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
    assert item.search_text == "Spool 305 M Corp 10 m 10m "
    assert item.name_folded == "Spool 305"


def test_each_field_is_kept_as_typed_and_respelt_where_they_differ() -> None:
    """Ali, 2026-11-15 (inventory-tng-c0c.2): what search keeps of a field, and
    what it matches of a search, is its fold as typed, then respelt if that
    differs."""
    assert folds("Cable 100 feet") == ("Cable 100 feet", "Cable 100ft")
    assert folds("Cap 10 µF") == ("Cap 10 mF", "Cap 10uF")
    assert folds("Spool 305m") == ("Spool 305m",)
    item = Item(name="Cable 100 feet", manufacturer="Cap 10 µF", part_number="X1")
    item.refold()
    assert item.search_text == "Cable 100 feet Cable 100ft Cap 10 mF Cap 10uF X1 "


def test_in_is_no_unit_and_words_never_spell_a_name() -> None:
    """Ali, 2026-11-15 (inventory-tng-c0c.2): "in" is a word, not an inch; a
    unit's word ("feet", "volts") is read by search, never to spell a name."""
    assert read("in") is None
    assert fold("2 in 1, 6 in") == "2 in 1, 6 in"
    assert fold("6 inch 6 inches 6 [in_i]") == "6[in_i] 6[in_i] 6[in_i]"
    for word in ("feet", "volts", "watt", "amps", "inch", "meters"):
        assert read(word) is not None, word
        assert units.readings(word) == [], word
    assert units.found_in("Cable 100 feet, 48 volts") == []


def test_what_was_found_before_units_were_read_still_is(
    admin_client: TestClient, session: Session
) -> None:
    """Ali, 2026-11-15 (inventory-tng-c0c.2): a unit's word alone, a unit typed
    shorter than a token not one, and a unit half typed find what they found
    before units were read; and a unit spelt otherwise finds it too."""
    parts = add(session, Category(name=f"Parts {tag()}"))
    for name, typed in (
        ("Cable 100 feet", ("feet", "100 feet", "100 ft", "100ft")),
        ("PoE injector 48 VDC", ("48 V", "48 VDC")),
        ("Wire 2.5 mm² red", ("2.5 mm", "mm²")),
        ("Load 50 Ω/m", ("50 Ω",)),
        ("Volts meter 5 volts", ("volts", "5 V", "5v")),
    ):
        item = add(session, Item(name=f"{name} {tag()}", category_id=parts.id))
        for each in typed:
            listed = page_text(admin_client.get(ITEMS, params={"q": each}))
            assert item.name_shown in listed, each


def test_a_search_mixing_spellings_is_matched_group_by_group(
    admin_client: TestClient, session: Session
) -> None:
    """Ali, 2026-11-15 (inventory-tng-ist.1): a row holding every
    group of a search, each as typed or respelt, is found, and ranks below
    one holding the search whole and above one only close to it."""
    parts = add(session, Category(name=f"Parts {tag()}"))
    whole, grouped, close, cap, wrong = (
        add(session, Item(name=f"{name} {tag()}", category_id=parts.id)).name_shown
        for name in (
            "PoE cable 100 ft 48 V",
            "Cable 100 feet, PoE 48 VDC",
            "Antenna, 1000 FT, 48A",
            "Cap 10uF 48 VDC",
            "Cable 100 feet, 12 VDC",
        )
    )

    def suggested(typed: str) -> list[str]:
        found = admin_client.get(f"{ITEMS}/suggest", params={"q": typed}).json()
        return [each["name"] for each in found]

    assert [
        name for name in suggested("100 ft 48 V") if name in (whole, grouped, close)
    ] == [whole, grouped, close]
    # A lone accent is no group.
    for typed in ("10 µF 48 V", "10uF 48V", "10uF \N{COMBINING ACUTE ACCENT} 48V"):
        listed = page_text(admin_client.get(ITEMS, params={"q": typed}))
        assert cap in listed, typed
    # Every group, not some: one holding only 100 feet is only close.
    found = suggested("100 feet 48 V")
    assert found.index(grouped) < found.index(wrong)


def test_a_search_finds_thousands_and_vdc_however_written(
    admin_client: TestClient, session: Session
) -> None:
    """Ali, 2026-11-15 (inventory-tng-ist.1)."""
    parts = add(session, Category(name=f"Parts {tag()}"))
    for name, typed in (
        ("Spool 1000 FT", ("1,000 ft", "1 000 ft", "1\N{THIN SPACE}000 ft")),
        ("Box 1,000 ft", ("1000 ft", "1 000 feet")),
        ("PSU 48 VDC", ("48V", "48 volts")),
        # Ali, 2026-11-15 (inventory-tng-4i1).
        ("Cable 0.500 m", ("0,500 m", "0,500m")),
        ("Cable 0,5 m", ("0.5 m",)),
    ):
        item = add(session, Item(name=f"{name} {tag()}", category_id=parts.id))
        assert item.name.startswith(name)
        for each in typed:
            listed = page_text(admin_client.get(ITEMS, params={"q": each}))
            assert item.name_shown in listed, each


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
            assert item.name_shown in listed, each
    assert any(
        found.item.name.startswith("Cap 10μF")
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


@pytest.mark.parametrize(
    ("typed", "shown"),
    [
        ("Spool 305 m", "305m · length"),
        ("Cap 10 uF", "10\u03bcF · capacitance"),
        ("Cap 10 \u039cF", "10\u03bcF · capacitance"),
        ("4.7 kohm", "4.7k\u03a9 · resistance"),
        ("Probe 85 deg C", "85\u00b0C · temperature"),
        ("Probe 85 Cel", "85\u00b0C · temperature"),
        ("Antenna -100 dBm", "-100dBm · power level"),
        ("Box 1,000 ft", "1,000ft · length"),
        ("Tank 2 m3", "2m\u00b3 · volume"),
        ("Radio 100 mhz", "100MHz · frequency"),
        # Data rates as printed.
        ("Link 1 Gbps", "1Gbps · data rate"),
        ("Link 1Gbit/s", "1Gbps · data rate"),
        # Megafarads are not likely, millifarads are.
        ("Cap 10MF", "10mF · capacitance"),
        # Both likely, but spelt as typed, so not asked.
        ("Pill 5 mg", "5mg · mass"),
        ("Resistor 10 MOhm", "10M\u03a9 · resistance"),
        # No kelvins likely (-269 °C); a bare K is a data rate.
        ("Hub 4 K", "4kbps · data rate"),
    ],
)
def test_a_name_is_saved_as_typed_and_its_units_read(typed: str, shown: str) -> None:
    """Ali, 2026-11-15 (inventory-tng-c0c.3): a reading changes what search
    matches, not the name; each said as its symbol joined to its number, and
    its kind."""
    (found,) = units.found_in(typed)
    assert found.read is not None
    assert f"{found.shown(found.read)} · {found.read.unit.kind}" == shown
    assert units.spell(typed, [found]) == typed
    # Searched alike: search reads the profile too.
    assert fold(units.read_as(typed)).lower() == fold(typed).lower()


def test_a_rule_respelling_a_unit_spells_the_name_by_its_symbol() -> None:
    """Saved as typed, unless its reading rule says to respell it."""
    rules = {"M": units.Rule("m", respell=True), "G": units.Rule("Gbit/s")}
    typed = "Tape 305 M, Box 10G"
    found = units.found_in(typed, rules)
    assert units.spell(typed, found) == "Tape 305m, Box 10G"


@pytest.mark.parametrize(
    "typed",
    [
        "LiteBeam 5AC Gen2",
        "Cat6a cable",
        "Cable Cat 6a, outdoor",
        "Patch Cat5e 2 m",
        "Patch Cat-6a 2 m",
        "Cat6a-patch",
        "Adapter 2-in-1",
        "2 g\u00e2teaux",
    ],
)
def test_a_product_is_never_read_as_a_unit(typed: str) -> None:
    """Cat6a is no 6 amperes, 5AC nothing (Ali, 2026-11-15, the product
    list); a unit after it still is."""
    assert [f.typed for f in units.found_in(typed)] == (
        ["2m"] if typed.endswith("2 m") else []
    )
    # Nor by search, as the forms do not.
    assert units.respell(typed).startswith(typed.removesuffix(" 2 m"))


def test_a_bare_g_m_or_k_may_be_a_data_rate() -> None:
    """Ali, 2026-11-15 (inventory-tng-c0c.3): 10G is 10 Gbps and 4 K 4 kbps
    without asking, the profile ranking grams and kelvins below; 3M may be 3
    Mbps or 3 meters, both ranked high, and 10g 10 Gbps or 10 grams, never
    silently grams: the words near it decide, else a form asks, offering data
    rates first, each by its unit in words, a data rate so."""
    for typed, expected in (("Box 10G", "Gbit/s"), ("Hub 4 K", "kbit/s")):
        (found,) = units.found_in(typed)
        assert (str(found.read), found.asks) == (expected, False), typed
    for typed, offered in (
        ("Tape 3M", ["3Mbps · data rate", "3m · meters"]),
        ("Box 100 M", ["100Mbps · data rate", "100m · meters"]),
        ("Box 10g", ["10Gbps · data rate", "10g · grams"]),
    ):
        (found,) = units.found_in(typed)
        assert found.asks, typed
        said = [f"{found.shown(r)} · {r.called}" for r in found.offered]
        assert said == offered
    for typed, expected in (
        ("Switch 100 M", "Mbit/s"),
        ("Spool 305M", "m"),
        ("SFP 10g", "Gbit/s"),
        ("Thermal paste 10g", "g"),
        ("Grease 500 g", "g"),
    ):
        (found,) = units.found_in(typed)
        assert (str(found.read), found.asks) == (expected, False), typed
    assert units.PROFILE[0] == "data rate"
    assert units.PROFILE[-1] == "mass"
    assert set(units.PROFILE) == set(KINDS)


def test_case_asks_only_between_two_plausible_readings() -> None:
    """5 MG may be megagrams or milligrams, both likely sizes, and neither
    spelt as typed: asked, by the token as typed; one rule settles it
    after every number."""
    typed = "Weights 5 MG, 10 MG"
    asked = [each for each in units.found_in(typed) if each.asks]
    assert [(each.number, each.token, each.key) for each in asked] == [
        ("5", "MG", "MG"),
        ("10", "MG", "MG"),
    ]
    assert [str(r) for r in asked[0].plausible] == ["Mg", "mg"]
    assert [r.named for r in asked[0].plausible] == ["megagrams", "milligrams"]
    first, second = units.found_in(typed, {"MG": units.Rule("mg")})
    assert (str(first.read), first.asks, str(second.read)) == ("mg", False, "mg")
    assert units.read_as(typed, {"MG": units.Rule("mg")}) == "Weights 5mg, 10mg"
    (none, _) = units.found_in(typed, {"MG": units.Rule(None)})
    assert (none.read, none.asks, none.offered) == (None, False, asked[0].plausible)
    assert units.read_as(typed, {"MG": units.Rule(None)}) == typed


def test_every_reading_is_shown_and_named_and_read_back_as_itself() -> None:
    """What a name stores is read as what it was (searches decompose it
    first, so m\u00b3 is m3), and every unit has a name in words."""
    assert set(units.NAMES) == {unit.symbol for unit in UNITS}
    every = [Reading(None, unit) for unit in UNITS] + [
        Reading(prefix, unit) for unit in UNITS if unit.prefixes for prefix in PREFIXES
    ]
    for reading in every:
        assert read(unicodedata.normalize("NFKD", reading.shown)) == reading
        assert "{" not in reading.named
    cubic = read("km3")
    assert cubic is not None
    assert cubic.named == "cubic kilometers"
    # In US spelling, as the app follows US conventions (inventory-tng-g6u).
    assert [getattr(read(t), "named", None) for t in ("mL", "t", "m")] == [
        "milliliters",
        "metric tons",
        "meters",
    ]


NB, THIN = "\u00a0", "\u202f"


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        # Ali's examples (2026-11-15, inventory-tng-ist.3).
        ("Cable 100ft 48 VDC", f"Cable 100{NB}ft 48{NB}VDC"),
        ("Cap 10uF 50V", f"Cap 10{NB}uF 50{NB}V"),
        ("Box 1,000 ft", f"Box 1000{NB}ft"),
        ("10,000 ft", f"10{THIN}000{NB}ft"),
        ("Spool 305M", f"Spool 305{NB}M"),
        # Thousands grouped from five digits, a decimal part and a sign kept.
        ("Reel 12345 ft", f"Reel 12{THIN}345{NB}ft"),
        ("Drum 1,234,567.5 mm", f"Drum 1{THIN}234{THIN}567.5{NB}mm"),
        ("Probe -40°C", f"Probe -40{NB}°C"),
        # Commas that group no thousands, as typed.
        ("Pack 1,2 m", f"Pack 1,2{NB}m"),
        # Nor one whose first group is a nought: no thousands, maybe a
        # decimal comma, not `0500`.
        ("Reel 0,500 m", f"Reel 0,500{NB}m"),
        ("Reel 00,500 m", f"Reel 00,500{NB}m"),
        # Units' words likewise, each as typed (Ali, 2026-11-15,
        # inventory-tng-tze).
        ("Box 1,000 feet", f"Box 1000{NB}feet"),
        ("Cable 100feet", f"Cable 100{NB}feet"),
        ("PoE 48VDC", f"PoE 48{NB}VDC"),
        ("Spool 10,000 metres", f"Spool 10{THIN}000{NB}metres"),
        ("Supply -12 Volts", f"Supply -12{NB}Volts"),
        ("Reel 0,500 metres", f"Reel 0,500{NB}metres"),
        ("Box 3 Feet", f"Box 3{NB}Feet"),
        # A number grouped by spaces, whole and as typed, as it may be a pack
        # count and a size, its ordinary spaces no-break so it never breaks
        # across a line; judged as search reads it, one number (Ali,
        # 2026-11-16, inventory-tng-t78).
        ("Box 1 000 feet", f"Box 1{NB}000{NB}feet"),
        ("Box 1 000 ft", f"Box 1{NB}000{NB}ft"),
        ("Cable 2 100 ft", f"Cable 2{NB}100{NB}ft"),
        ("Reel 12 345 678.5 mm", f"Reel 12{NB}345{NB}678.5{NB}mm"),
        ("X-1 000 feet", "X-1 000 feet"),
        ("Cat 6 305 m", f"Cat 6 305{NB}m"),
        # A product's name before any space is one still (inventory-tng-uxo6).
        (f"Cat{NB}6 305 m", f"Cat{NB}6 305{NB}m"),
        ("Cable 2 1,000 ft", f"Cable 2 1000{NB}ft"),
        # Its sign kept with it, and grouped by any space search reads as
        # one; no-break and thin spaces as typed.
        ("Supply -1 000 V", f"Supply -1{NB}000{NB}V"),
        ("Supply \u22121 000 V", f"Supply \u22121{NB}000{NB}V"),
        ("Box 1\u202f000 ft", f"Box 1\u202f000{NB}ft"),
        (f"Box 1{NB}000 ft", f"Box 1{NB}000{NB}ft"),
        # A unit after any space found as after a plain one, a no-break
        # space before it (inventory-tng-vw0f).
        (f"Cap 1000{NB}\u03bcF", f"Cap 1000{NB}\u03bcF"),
        (f"Cap 1000{THIN}\u03bcF", f"Cap 1000{NB}\u03bcF"),
        ("Cap 1000\u1680\u03bcF", f"Cap 1000{NB}\u03bcF"),
        (f"Box 1{THIN}000{THIN}ft", f"Box 1{THIN}000{NB}ft"),
        (f"Box 1 000{NB}feet", f"Box 1{NB}000{NB}feet"),
        ("Cable 2\u2009100 ft", f"Cable 2\u2009100{NB}ft"),
        ("Reel 1\u2009000 000 mm", f"Reel 1\u2009000{NB}000{NB}mm"),
        # Products, units no rule or word settles, and tokens no unit, as
        # typed.
        ("Cat6a cable 5AC SFP+", "Cat6a cable 5AC SFP+"),
        ("Box 3M", "Box 3M"),
        ("Bolt 24P", "Bolt 24P"),
        # Spaced already with a no-break space, as typed.
        (f"Cable 100{NB}ft", f"Cable 100{NB}ft"),
        # A number within a word or a code, as typed; one in brackets not.
        (
            "UF-SM-10G ES-8-150W ab07f 2x10G 1/10G",
            "UF-SM-10G ES-8-150W ab07f 2x10G 1/10G",
        ),
        ("Patch (3ft)", f"Patch (3{NB}ft)"),
        # Nor one whose unit runs on into a code or another number.
        ("10G-SFP+ module", "10G-SFP+ module"),
        ("48V-to-12V converter", "48V-to-12V converter"),
        ("48VDC-to-12VDC converter", "48VDC-to-12VDC converter"),
        ("ES-8-100feet x2feet", "ES-8-100feet x2feet"),
        ("Tape 12 inch/30 cm", "Tape 12 inch/30 cm"),
        ("Radio 10GHz/20 MHz", "Radio 10GHz/20 MHz"),
        ("Box 3ft+", f"Box 3{NB}ft+"),
    ],
)
def test_a_name_is_shown_with_each_number_and_unit_spaced(
    name: str, shown: str
) -> None:
    """Each number and the unit read after it are spaced by a no-break space,
    the unit as typed; thousands without commas, from five digits grouped by
    thin no-break spaces (Ali, 2026-11-15, inventory-tng-ist.3)."""
    assert units.shown_name(name) == shown


def test_a_name_is_shown_as_its_rules_read_it() -> None:
    """A unit read by its rule is spaced, one a rule says is no unit not:
    `3M` near nothing is asked, so as typed, until a rule reads it, and a
    unit's word a rule says is no unit not either."""
    assert units.shown_name("Box 3 feet", {"feet": units.Rule(None)}) == "Box 3 feet"
    length = units.Rule("m")
    assert units.shown_name("Box 3M", {"M": length}) == f"Box 3{NB}M"
    assert units.shown_name("Cap 10uF", {"uF": units.Rule(None)}) == "Cap 10uF"
    # A rule respelling its unit spells the name saved, which is shown so.
    saved = units.spell(
        "Box 3M", units.found_in("Box 3M", {"M": units.Rule("m", True)})
    )
    assert units.shown_name(saved, {"M": units.Rule("m", True)}) == f"Box 3{NB}m"


def test_every_kind_of_unit_has_a_plausible_size_and_words() -> None:
    # A kind missing fails only when a page shows one.
    assert {unit.kind for unit in UNITS} == set(KINDS)
