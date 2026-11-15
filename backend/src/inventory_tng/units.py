"""The units this inventory's names use, and how searches spell them
(docs/decisions/0021, 2026-11-14, inventory-tng-c0c).

One row per unit: its canonical symbol, in ASCII, its kind of quantity,
whether SI prefixes apply, and the spellings it is also read by, every
UCUM code among them, so the UCUM form always works. Prefixed forms are
derived, never rows. A unit is read only right after a number, and in any
case (Ali, 2026-11-12): `10µF`, `10 μF` and `10uf` are all `10uF`, and
`85 °C`, `85℃` and `85 Cel` are `85degC`. Elsewhere only a unit spelt with
a sign no word is (`μF`, `°C`, `kΩ`) is read, so a Greek word is folded as
any other.

The scan page reads the same table (frontend/src/units.json, which `uv run
python -m inventory_tng.units` writes) the same way (frontend/src/units.ts),
and both are tested against frontend/src/units-vectors.json.

An item's or a package's name, as typed on its form, is stored with each
unit after a number spelt by its symbol as a name shows it, joined to the
number (spell, inventory-tng-c0c.3): case is read loosely only where one
reading is a likely size, and the form asks where two are.

A reading rule (models.ReadingRule, inventory-tng-c0c.4) settles how a
number and its unit, as typed (`305M`, `10G`), are read, or that they are
no unit (`16P`): looked up before the table, wherever a unit after a
number is read, by search here and on the scan page, and by the forms.
"""

import json
import re
import sys
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from inventory_tng.local_env import CLONE

# Raise it whenever a change to the table reads any text otherwise, or
# search keeps it otherwise (models.held): rows record what folded them
# (models.FOLDED_WITH), so those folded before can be folded again, and a
# test fails until it is raised. 2: each field kept as typed too, and "in"
# no unit (Ali, 2026-11-15).
VERSION = 2

TABLE = CLONE / "frontend" / "src" / "units.json"


@dataclass(frozen=True)
class Kind:
    """A kind of quantity: the unit its sizes are measured in, and a size
    that is plausible for this inventory, low to high, in that unit."""

    base: str
    plausible: tuple[float, float]


@dataclass(frozen=True)
class Prefix:
    """An SI prefix: its symbol, in ASCII, its power of ten, its name, the
    other spellings it is read by, and its symbol in a name, if not
    `symbol`."""

    symbol: str
    exponent: int
    name: str
    spellings: tuple[str, ...] = ()
    shown: str | None = None


@dataclass(frozen=True)
class Unit:
    """A unit, as `symbol` is spelt in what searches compare.

    `ucum` are its UCUM codes and `spellings` its other symbols: with the
    symbol, each takes a prefix if `prefixes` says so. `words` never take
    one, and are read only by search, never to spell a name (Ali,
    2026-11-15). A size in it is `factor` times the size (its prefix's
    power `power` times, as for m3) + `offset` in its kind's base; a level
    in decibels has no factor."""

    symbol: str
    kind: str
    prefixes: bool
    ucum: tuple[str, ...] = ()
    spellings: tuple[str, ...] = ()
    words: tuple[str, ...] = ()
    factor: float | None = 1.0
    offset: float = 0.0
    power: int = 1

    def prefixed(self) -> tuple[str, ...]:
        """The spellings a prefix may go before, the symbol first."""
        return tuple(dict.fromkeys((self.symbol, *self.ucum, *self.spellings)))

    def takes_prefix(self) -> tuple[str, ...]:
        """The spellings read after a prefix, if it takes one."""
        return self.prefixed() if self.prefixes else ()

    def alone(self) -> tuple[str, ...]:
        """The spellings read only unprefixed: its words, and its symbols if
        it takes no prefix."""
        return (*(() if self.prefixes else self.prefixed()), *self.words)


# Ali, 2026-11-12: this inventory's kinds, each with one plausible size.
KINDS: dict[str, Kind] = {
    "length": Kind("m", (1e-4, 1e5)),
    "frequency": Kind("Hz", (1.0, 1e12)),
    "data rate": Kind("bit/s", (1e3, 1e12)),
    "power": Kind("W", (1e-6, 1e5)),
    # Power as a level, and an antenna's gain, both in decibels.
    "power level": Kind("dBm", (-120.0, 60.0)),
    "gain": Kind("dBi", (-10.0, 50.0)),
    "voltage": Kind("V", (1e-3, 1e5)),
    "current": Kind("A", (1e-6, 1e3)),
    "temperature": Kind("degC", (-100.0, 1000.0)),
    "capacitance": Kind("F", (1e-15, 1e3)),
    "resistance": Kind("Ohm", (1e-3, 1e9)),
    "mass": Kind("g", (1e-3, 1e7)),
    "volume": Kind("L", (1e-3, 1e4)),
}

# The SI's, largest first, as UCUM lists them; ronna, quetta, ronto and
# quecto (2022) are newer than UCUM 2.2. Micro is spelt "u", as in UCUM.
PREFIXES: tuple[Prefix, ...] = (
    Prefix("Q", 30, "quetta"),
    Prefix("R", 27, "ronna"),
    Prefix("Y", 24, "yotta"),
    Prefix("Z", 21, "zetta"),
    Prefix("E", 18, "exa"),
    Prefix("P", 15, "peta"),
    Prefix("T", 12, "tera"),
    Prefix("G", 9, "giga"),
    Prefix("M", 6, "mega"),
    Prefix("k", 3, "kilo"),
    Prefix("h", 2, "hecto"),
    Prefix("da", 1, "deca"),
    Prefix("d", -1, "deci"),
    Prefix("c", -2, "centi"),
    Prefix("m", -3, "milli"),
    Prefix("u", -6, "micro", ("\u03bc", "\u00b5"), "\u03bc"),  # Greek mu, micro sign
    Prefix("n", -9, "nano"),
    Prefix("p", -12, "pico"),
    Prefix("f", -15, "femto"),
    Prefix("a", -18, "atto"),
    Prefix("z", -21, "zepto"),
    Prefix("y", -24, "yocto"),
    Prefix("r", -27, "ronto"),
    Prefix("q", -30, "quecto"),
)

UNITS: tuple[Unit, ...] = (
    Unit("m", "length", True, ("m",), (), ("meter", "meters", "metre", "metres")),
    # Not "in", a word more often than an inch (Ali, 2026-11-15).
    Unit("[in_i]", "length", False, ("[in_i]",), (), ("inch", "inches"), 0.0254),
    Unit("ft", "length", False, ("[ft_i]",), (), ("foot", "feet"), 0.3048),
    Unit("yd", "length", False, ("[yd_i]",), (), ("yard", "yards"), 0.9144),
    Unit("mi", "length", False, ("[mi_i]",), (), ("mile", "miles"), 1609.344),
    Unit("Hz", "frequency", True, ("Hz",), (), ("hertz",)),
    Unit("bit/s", "data rate", True, ("bit/s",), ("bps", "b/s")),
    Unit("W", "power", True, ("W",), (), ("watt", "watts")),
    Unit("dBm", "power level", False, factor=None),
    Unit("dBi", "gain", False, factor=None),
    Unit("V", "voltage", True, ("V",), (), ("volt", "volts")),
    Unit("A", "current", True, ("A",), (), ("amp", "amps", "ampere", "amperes")),
    # Celsius as degC (Ali, 2026-11-12), the convention in ASCII.
    Unit("degC", "temperature", False, ("Cel",), ("°C", "deg C")),
    Unit(
        "degF",
        "temperature",
        False,
        ("[degF]",),
        ("°F", "deg F"),
        factor=5 / 9,
        offset=-160 / 9,
    ),
    Unit("K", "temperature", True, ("K",), (), ("kelvin",), offset=-273.15),
    Unit("F", "capacitance", True, ("F",), (), ("farad", "farads")),
    Unit("Ohm", "resistance", True, ("Ohm",), ("\u03a9", "ohm", "ohms")),
    Unit("g", "mass", True, ("g",), (), ("gram", "grams")),
    Unit("t", "mass", False, ("t",), (), ("tonne", "tonnes"), 1e6),
    Unit("lb", "mass", False, ("[lb_av]",), ("lbs",), ("pound", "pounds"), 453.59237),
    Unit("oz", "mass", False, ("[oz_av]",), (), ("ounce", "ounces"), 28.349523125),
    Unit("L", "volume", True, ("L", "l"), (), ("liter", "liters", "litre", "litres")),
    Unit("m3", "volume", True, ("m3",), factor=1000.0, power=3),
    Unit("gal", "volume", False, ("[gal_us]",), (), ("gallon", "gallons"), 3.785411784),
)

# Each unit's name, its prefix's where "{}" is, and its symbol in a name
# where that is not its symbol here: as the SI Brochure writes it, in the
# spelling models.canonical keeps (docs/decisions/0021, 2026-11-14).
NAMES: dict[str, str] = {
    "m": "{}metres",
    "[in_i]": "inches",
    "ft": "feet",
    "yd": "yards",
    "mi": "miles",
    "Hz": "{}hertz",
    "bit/s": "{}bits per second",
    "W": "{}watts",
    "dBm": "decibel-milliwatts",
    "dBi": "decibels isotropic",
    "V": "{}volts",
    "A": "{}amperes",
    "degC": "degrees Celsius",
    "degF": "degrees Fahrenheit",
    "K": "{}kelvins",
    "F": "{}farads",
    "Ohm": "{}ohms",
    "g": "{}grams",
    "t": "tonnes",
    "lb": "pounds",
    "oz": "ounces",
    "L": "{}litres",
    "m3": "cubic {}metres",
    "gal": "US gallons",
}
# Data rates as printed: bps, Mbps, Gbps (Ali, 2026-11-15, Q15).
SHOWN: dict[str, str] = {
    "degC": "°C",
    "degF": "°F",
    "Ohm": "\u03a9",
    "m3": "m\u00b3",
    "bit/s": "bps",
}


def table() -> dict[str, Any]:
    """What reading a unit needs, as the scan page reads it (units.json):
    each prefix's spellings and each unit's, in the order they are tried,
    the symbol first."""
    return {
        "version": VERSION,
        "prefixes": [[prefix.symbol, *prefix.spellings] for prefix in PREFIXES],
        "units": [
            {
                "symbol": unit.symbol,
                "prefixed": list(unit.takes_prefix()),
                "alone": list(unit.alone()),
            }
            for unit in UNITS
        ],
    }


@dataclass(frozen=True)
class Reading:
    """A unit as read: its prefix, if any, and the unit."""

    prefix: Prefix | None
    unit: Unit

    def __str__(self) -> str:
        return (self.prefix.symbol if self.prefix else "") + self.unit.symbol

    @property
    def shown(self) -> str:
        """As a name spells it: `μF`, `kΩ`, `°C`."""
        prefix = (self.prefix.shown or self.prefix.symbol) if self.prefix else ""
        return prefix + SHOWN.get(self.unit.symbol, self.unit.symbol)

    @property
    def named(self) -> str:
        """In words: `microfarads`, `cubic kilometres`."""
        return NAMES[self.unit.symbol].format(self.prefix.name if self.prefix else "")

    def plausible(self, number: float) -> bool:
        """Whether `number` of it is a size this inventory may hold: in its
        kind's plausible range, as a size in the kind's base, or for a
        level in decibels, as typed."""
        low, high = KINDS[self.unit.kind].plausible
        unit = self.unit
        if unit.factor is None:
            return low <= number <= high
        power = self.prefix.exponent * unit.power if self.prefix else 0
        size = number * 10.0**power * unit.factor + unit.offset
        # A size of a kind that cannot be negative, typed so, as its size.
        return low <= (abs(size) if low > 0 else size) <= high


def _lowered(pairs: list[tuple[str, Unit]]) -> dict[str, Unit]:
    """The unit each spelling reads as in any case: one, as no two units
    share a spelling in any case."""
    return {spelling.lower(): unit for spelling, unit in pairs}


_PREFIXED = [(s, unit) for unit in UNITS for s in unit.takes_prefix()]
_ALONE = [(s, unit) for unit in UNITS for s in unit.alone()]
# Every unspaced spelling, unprefixed, as typed and in lower case.
_WHOLE = dict([*_PREFIXED, *_ALONE])
_WHOLE_LOOSE = _lowered([*_PREFIXED, *_ALONE])
_TAKES_PREFIX_LOOSE = _lowered(_PREFIXED)
# Every symbol and spelling, not words, in lower case (readings).
_SPELT_LOOSE = _lowered([(s, unit) for unit in UNITS for s in unit.prefixed()])
_PREFIX_SPELLINGS = [
    (spelling, prefix)
    for prefix in PREFIXES
    for spelling in (prefix.symbol, *prefix.spellings)
]


def read(token: str) -> Reading | None:
    """The unit `token` spells, if any: as typed first, then with its
    prefix as typed and the unit in any case, then all in any case. So
    `MHz` is megahertz and `mhz` millihertz, which a search, ignoring case,
    takes as one."""
    if unit := _WHOLE.get(token):
        return Reading(None, unit)
    for spelling, prefix in _PREFIX_SPELLINGS:
        if token.startswith(spelling):
            rest = token[len(spelling) :]
            if unit := _TAKES_PREFIX_LOOSE.get(rest.lower()):
                return Reading(prefix, unit)
    lower = token.lower()
    if unit := _WHOLE_LOOSE.get(lower):
        return Reading(None, unit)
    for spelling, prefix in _PREFIX_SPELLINGS:
        if lower.startswith(spelling.lower()):
            rest = lower[len(spelling) :]
            if unit := _TAKES_PREFIX_LOOSE.get(rest):
                return Reading(prefix, unit)
    return None


@dataclass(frozen=True)
class Rule:
    """A reading rule's verdict (models.ReadingRule): the reading, as
    str(Reading), or None, no unit; and whether a name typed with it spells
    it by its symbol."""

    reading: str | None
    respell: bool = False


# Reading rules by what each is kept by (rule_key).
type Rules = Mapping[str, Rule]


def rule_key(number: str, token: str) -> str:
    """What a reading rule is kept by: a number and its unit as typed,
    joined, in canonical spelling (NFC), so `305 M` and `305M` are one."""
    return unicodedata.normalize("NFC", number + token)


def _number(text: str, end: int) -> str:
    """The number ending at `end` in `text`, as typed."""
    number = _NUMBER.search(text[:end])
    assert number is not None  # It ends at a digit AFTER_A_NUMBER found.
    return number[0]


def _key_of(match: re.Match[str]) -> str:
    """The rule_key of a unit AFTER_A_NUMBER found."""
    return rule_key(_number(match.string, match.end(1)), match[2])


# What may be a unit, and that it ends where a letter or digit does not
# follow: frontend/src/units.ts's, character for character.
_TOKEN = (
    r"(?:[A-Za-z\u00b0\u00b5\u03bc\u039c\u03a9\u03c9]|\[[A-Za-z_]+\])+[0-9]?"
    r"(?:/[A-Za-z]+)?"
)
_ENDS = r"(?![^\W_])"
# A digit, perhaps a space, then what may be a unit.
AFTER_A_NUMBER = re.compile(rf"([0-9]) ?([Dd][Ee][Gg] [CFcf]|{_TOKEN}){_ENDS}")
# What may be a unit with no number before it: read only if spelt with a
# sign no word is (°, μ, Ω), so "μF" finds "10μF", as "Cel" or "m" alone
# do not, which may be words (Ali, 2026-11-12).
ALONE = re.compile(rf"(?<![^\W_])({_TOKEN}){_ENDS}")


def _marked(text: str, *at: int) -> bool:
    """Whether a combining mark is at any of `at` in `text`: decomposed, a
    letter's accent is one, so a token beside it is part of a word, as ω in
    ώρα is."""
    return any(
        0 <= i < len(text) and unicodedata.category(text[i])[0] == "M" for i in at
    )


def respell(text: str, rules: Rules | None = None) -> str:
    """`text` with each unit read right after a number spelt by its symbol
    (str(Reading)), joined to the number, as its reading rule says if it has
    one (`rules`), and each unit alone spelt with a sign no word is (not
    ASCII) by its symbol. `text` is decomposed (NFKD), as both the server and
    the scan page fold it, so `℃` is `°C` and `µ` `μ`."""

    def respelt(match: re.Match[str]) -> str:
        if _marked(match.string, match.end()):
            return match[0]
        if rules and (rule := rules.get(_key_of(match))) is not None:
            return match[0] if rule.reading is None else f"{match[1]}{rule.reading}"
        reading = read(match[2])
        return match[0] if reading is None else f"{match[1]}{reading}"

    def alone(match: re.Match[str]) -> str:
        if match[1].isascii() or _marked(match.string, match.start() - 1, match.end()):
            return match[0]
        reading = read(match[1])
        return match[0] if reading is None else str(reading)

    return ALONE.sub(alone, AFTER_A_NUMBER.sub(respelt, text))


# How a name an administrator types is read (docs/decisions/0021, Ali,
# 2026-11-15, option D, inventory-tng-c0c.3): saved as typed, each unit
# right after a number read by its reading rule, else by the one likely
# reading the table leaves, else asked once, the answer kept as a rule.

# The number a unit follows, ending at its last digit: a comma groups
# thousands, a point is decimal, and a minus counts only where no letter
# or digit is before it ("-40 dBm", not "Gen2-5 m").
_NUMBER = re.compile(r"(?:(?<![^\W_])[-\u2212])?[0-9][0-9,]*(?:[.][0-9]+)?\Z")

# A network inventory's profile (Ali, 2026-11-15): where a unit may be of
# several kinds, the readings are offered in this order of kinds, data
# rates first and mass last.
PROFILE = (
    "data rate",
    "length",
    "power level",
    "gain",
    "power",
    "voltage",
    "current",
    "frequency",
    "temperature",
    "capacitance",
    "resistance",
    "volume",
    "mass",
)
# A bare G, M or K after a number may be a data rate: 10G is 10 Gbps.
BARE_RATES = {"G": "G", "M": "M", "K": "k"}
# Words in names that are products, never a number and its unit (Ali,
# 2026-11-15), compared in lower case: Cat6a is no 6 amperes.
PRODUCTS = frozenset(
    (
        "cat5",
        "cat5e",
        "cat6",
        "cat6a",
        "cat7",
        "cat8",
        "sfp",
        "sfp+",
        "sfp28",
        "qsfp",
        "qsfp+",
        "qsfp28",
        "poe",
        "poe+",
        "2ac",
        "5ac",
        "gen2",
        "gen3",
        "2-in-1",
        "3-in-1",
        "4-in-1",
    )
)
# A word, as PRODUCTS has them: letters, digits, + and - within.
_WORD = re.compile(r"[A-Za-z0-9+]+(?:-[A-Za-z0-9+]+)*")


def reading_of(symbol: str) -> Reading | None:
    """The reading spelt `symbol` (str(Reading)), if one is."""
    reading = read(symbol)
    return reading if reading is not None and str(reading) == symbol else None


def readings(token: str) -> list[tuple[Reading, bool]]:
    """Every unit `token` spells in any case, each once, a bare G, M or K
    as a data rate too (BARE_RATES), and whether it is spelt so as typed:
    those first, then by PROFILE's kinds. `MF` is megafarads as typed and
    millifarads otherwise."""
    lower = token.lower()
    found: list[tuple[Reading, bool]] = []
    # A word ("feet", "volts") never spells a name (Ali, 2026-11-15).
    if unit := _SPELT_LOOSE.get(lower):
        found.append((Reading(None, unit), token in unit.prefixed()))
    # One unit at most per prefix, as no two share a spelling in any case.
    for spelling, prefix in _PREFIX_SPELLINGS:
        if lower.startswith(spelling.lower()):
            rest = token[len(spelling) :]
            if unit := _TAKES_PREFIX_LOOSE.get(rest.lower()):
                exact = token.startswith(spelling) and rest in unit.prefixed()
                found.append((Reading(prefix, unit), exact))
    if token in BARE_RATES:
        rate = read(BARE_RATES[token] + "bit/s")
        assert rate is not None  # Gbit/s, Mbit/s and kbit/s are read.
        found.append((rate, False))
    return sorted(found, key=lambda pair: (not pair[1], _rank(pair[0])))


def _rank(reading: Reading) -> int:
    return PROFILE.index(reading.unit.kind)


def _product(name: str, at: int) -> bool:
    """Whether the word the digit at `at` is in, alone or joined to the word
    before it, is a product's (PRODUCTS): `Cat6a`, `Cat 6a`, `5AC`."""
    for word in _WORD.finditer(name):
        if word.start() <= at < word.end():
            before = re.search(r"([A-Za-z0-9+]+)[ -]?\Z", name[: word.start()])
            joined = (before[1] if before else "") + word[0]
            return word[0].lower() in PRODUCTS or joined.lower() in PRODUCTS
    return False  # pragma: no cover  A digit is always in a word.


@dataclass(frozen=True)
class Found:
    """What may be a unit right after a number in a name: the number as
    typed, the unit as typed (`token`), where the space before it, if any,
    and it are (`start`, `end`), and the readings of it that are plausible
    sizes (Reading.plausible), the one spelt as typed first, then by
    PROFILE. `read` is what it reads as: its rule's reading (None: no
    unit), else the one plausible, or the one plausible spelt as typed;
    `rule` its rule, if it has one."""

    number: str
    token: str
    start: int
    end: int
    plausible: tuple[Reading, ...]
    read: Reading | None
    rule: Rule | None = None

    @property
    def key(self) -> str:
        """What its reading rule is kept by (rule_key)."""
        return rule_key(self.number, self.token)

    @property
    def asks(self) -> bool:
        """Whether nothing settles it, two readings being likely: no rule,
        and case or the profile leaving two. A form asks once."""
        return self.rule is None and self.read is None and len(self.plausible) > 1

    @property
    def offered(self) -> tuple[Reading, ...]:
        """What it may be read as: its plausible readings, and its rule's,
        if that is not among them."""
        if self.read is None or self.read in self.plausible:
            return self.plausible
        return (*self.plausible, self.read)

    def shown(self, reading: Reading) -> str:
        """`reading` joined to its number, as a name spells it: `10Gbps`."""
        return self.number + reading.shown


def found_in(name: str, rules: Rules | None = None) -> list[Found]:
    """What may be units in `name`, right after a number: each token some
    unit spells, in any case (readings), whether or not one is plausible,
    and each a reading rule names, but in a product's name (PRODUCTS). Its
    rule, if any, settles it. `name` is in canonical spelling
    (models.canonical), so `℃` is `°C`."""
    found = []
    for match in AFTER_A_NUMBER.finditer(name):
        if _marked(name, match.end()):
            continue
        number = _number(name, match.end(1))
        rule = (rules or {}).get(rule_key(number, match[2]))
        every = readings(match[2])
        if rule is None and (not every or _product(name, match.end(1) - 1)):
            continue
        value = float(number.replace(",", "").replace("\u2212", "-"))
        plausible = [(r, exact) for r, exact in every if r.plausible(value)]
        exact = [r for r, spelt in plausible if spelt]
        settled = None
        if rule is not None:
            settled = None if rule.reading is None else reading_of(rule.reading)
        elif len(plausible) == 1:
            settled = plausible[0][0]
        elif len(exact) == 1:
            settled = exact[0]
        found.append(
            Found(
                number,
                match[2],
                match.end(1),
                match.end(),
                tuple(r for r, _ in plausible),
                settled,
                rule,
            )
        )
    return found


def read_as(name: str, rules: Rules | None = None) -> str:
    """`name` with each unit after a number that is read, by its rule or
    the table (found_in), spelt by its symbol (str(Reading)) joined to its
    number, the rest as it is: so names that read alike are spelt alike,
    `10 µF`, `10 uF` and `10μF` all `10uF` (models.KeyedName.unit_key)."""
    return _spelt(name, {f: str(f.read) for f in found_in(name, rules) if f.read})


def spell(name: str, found: list[Found]) -> str:
    """`name` as it is saved: as typed (Ali, 2026-11-15), but where a
    reading rule says to respell its unit, by its symbol as a name shows it
    (Reading.shown), joined to its number."""
    return _spelt(
        name,
        {f: f.read.shown for f in found if f.read and f.rule and f.rule.respell},
    )


def _spelt(name: str, spelt: Mapping[Found, str]) -> str:
    """`name` with each Found's unit, and the space before it, as `spelt`
    says."""
    parts, at = [], 0
    for found, spelling in spelt.items():
        parts += [name[at : found.start], spelling]
        at = found.end
    return "".join([*parts, name[at:]])


def main(path: Path = TABLE) -> None:
    """Write units.json, which the scan page reads; the task then lays it
    out as Biome does."""
    text = json.dumps(table(), indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")
    print(f"Wrote {path}")


if __name__ == "__main__":
    main(*(Path(arg) for arg in sys.argv[1:2]))
