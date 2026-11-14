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

# Raise it whenever a change to the table reads any text otherwise: rows
# record what folded them (models.FOLDED_WITH), so those folded before can
# be folded again, and a test fails until it is raised.
VERSION = 1

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
    one. A size in it is `factor` times the size (its prefix's power `power`
    times, as for m3) + `offset` in its kind's base; a level in decibels has
    no factor."""

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
    Unit("in", "length", False, ("[in_i]",), (), ("inch", "inches"), 0.0254),
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
    "in": "inches",
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
SHOWN: dict[str, str] = {"degC": "°C", "degF": "°F", "Ohm": "\u03a9", "m3": "m\u00b3"}


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
                "prefixed": list(unit.prefixed()) if unit.prefixes else [],
                "alone": [
                    *([] if unit.prefixes else unit.prefixed()),
                    *unit.words,
                ],
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


def _first(pairs: list[tuple[str, Unit]]) -> dict[str, Unit]:
    """The unit each spelling reads as: the first unit spelt so."""
    found: dict[str, Unit] = {}
    for spelling, unit in pairs:
        found.setdefault(spelling, unit)
    return found


_ALONE = [
    (spelling, unit)
    for unit in UNITS
    for spelling in (unit.words if unit.prefixes else (*unit.prefixed(), *unit.words))
]
_PREFIXED = [(s, unit) for unit in UNITS if unit.prefixes for s in unit.prefixed()]
# Every unspaced spelling, unprefixed, as typed and in lower case.
_WHOLE = _first([*_PREFIXED, *_ALONE])
_WHOLE_LOOSE = _first([(s.lower(), unit) for s, unit in [*_PREFIXED, *_ALONE]])
_TAKES_PREFIX = _first(_PREFIXED)
_TAKES_PREFIX_LOOSE = _first([(s.lower(), unit) for s, unit in _PREFIXED])
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
            unit = _TAKES_PREFIX.get(rest) or _TAKES_PREFIX_LOOSE.get(rest.lower())
            if unit:
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


def respell(text: str) -> str:
    """`text` with each unit read right after a number spelt by its symbol
    (str(Reading)), joined to the number, and each unit alone spelt with a
    sign no word is (not ASCII) by its symbol. `text` is decomposed (NFKD),
    as both the server and the scan page fold it, so `℃` is `°C` and `µ`
    `μ`."""

    def respelt(match: re.Match[str]) -> str:
        if _marked(match.string, match.end()):
            return match[0]
        reading = read(match[2])
        return match[0] if reading is None else f"{match[1]}{reading}"

    def alone(match: re.Match[str]) -> str:
        if match[1].isascii() or _marked(match.string, match.start() - 1, match.end()):
            return match[0]
        reading = read(match[1])
        return match[0] if reading is None else str(reading)

    return ALONE.sub(alone, AFTER_A_NUMBER.sub(respelt, text))


# How a name an administrator types spells its units (docs/decisions/0021,
# Ali, 2026-11-12, inventory-tng-c0c.3): each unit right after a number
# by its symbol, joined to the number, read in any case, but only as a
# size this inventory may hold; where case leaves two such, the form asks.

# The number a unit follows, ending at its last digit: a comma groups
# thousands, a point is decimal, and a minus counts only where no letter
# or digit is before it ("-40 dBm", not "Gen2-5 m").
_NUMBER = re.compile(r"(?:(?<![^\W_])[-\u2212])?[0-9][0-9,]*(?:[.][0-9]+)?\Z")


def readings(token: str) -> list[tuple[Reading, bool]]:
    """Every unit `token` spells in any case, each once, and whether it is
    spelt so as typed, those first: `MF` is megafarads as typed and
    millifarads otherwise."""
    lower = token.lower()
    found: dict[str, tuple[Reading, bool]] = {}

    def add(reading: Reading, exact: bool) -> None:
        if exact or str(reading) not in found:
            found[str(reading)] = (reading, exact)

    for spelling, unit in [*_PREFIXED, *_ALONE]:
        if spelling.lower() == lower:
            add(Reading(None, unit), spelling == token)
    for spelling, prefix in _PREFIX_SPELLINGS:
        if lower.startswith(spelling.lower()):
            rest = token[len(spelling) :]
            for each, unit in _PREFIXED:
                if each.lower() == rest.lower():
                    exact = token.startswith(spelling) and each == rest
                    add(Reading(prefix, unit), exact)
    return sorted(found.values(), key=lambda pair: not pair[1])


@dataclass(frozen=True)
class Found:
    """What may be a unit right after a number in a name: the number as
    typed, the unit as typed (`token`), where the space before it, if any,
    and it are (`start`, `end`), and the readings of it that are plausible
    sizes (Reading.plausible), the one spelt as typed first. `settled` is
    the reading to spell it by, if case leaves only one: the one plausible,
    or the one plausible spelt as typed."""

    number: str
    token: str
    start: int
    end: int
    plausible: tuple[Reading, ...]
    settled: Reading | None

    @property
    def asks(self) -> bool:
        """Whether case leaves two plausible readings, so the form asks."""
        return self.settled is None and len(self.plausible) > 1


def found_in(name: str) -> list[Found]:
    """What may be units in `name`, right after a number: each token some
    unit spells, in any case (readings), whether or not one is plausible.
    `name` is in canonical spelling (models.canonical), so `℃` is `°C`."""
    found = []
    for match in AFTER_A_NUMBER.finditer(name):
        every = readings(match[2])
        if _marked(name, match.end()) or not every:
            continue
        number = _NUMBER.search(name[: match.end(1)])
        assert number is not None  # It ends at the digit the match starts with.
        value = float(number[0].replace(",", "").replace("\u2212", "-"))
        plausible = [(r, exact) for r, exact in every if r.plausible(value)]
        exact = [r for r, spelt in plausible if spelt]
        settled = None
        if len(plausible) == 1:
            settled = plausible[0][0]
        elif len(exact) == 1:
            settled = exact[0]
        found.append(
            Found(
                number[0],
                match[2],
                match.end(1),
                match.end(),
                tuple(r for r, _ in plausible),
                settled,
            )
        )
    return found


def spell(name: str, chosen: Mapping[str, str]) -> tuple[str, list[Found]]:
    """`name` with each unit right after a number spelt by its symbol as a
    name shows it (Reading.shown), joined to the number: the one reading
    case leaves, or the one `chosen` for its token (by str(Reading)), as
    the form asked. And what is still to be asked. A token with no
    plausible reading is left as typed: `4 K`, as -269 °C, is no unit."""
    spelt, asked, at = [], [], 0
    for found in found_in(name):
        choice = next(
            (r for r in found.plausible if str(r) == chosen.get(found.token)), None
        )
        reading = found.settled or choice
        if reading is None:
            if found.asks:
                asked.append(found)
            continue
        spelt += [name[at : found.start], reading.shown]
        at = found.end
    return "".join([*spelt, name[at:]]), asked


def laid_out(value: object, depth: int = 0) -> str:
    """`value` in JSON as Biome lays it out: a list of values on one line."""
    if isinstance(value, dict):
        lines = [
            f"{json.dumps(key)}: {laid_out(v, depth + 1)}" for key, v in value.items()
        ]
    elif isinstance(value, list) and any(isinstance(v, list | dict) for v in value):
        lines = [laid_out(v, depth + 1) for v in value]
    else:
        return json.dumps(value, ensure_ascii=False)
    inside = "  " * (depth + 1)
    opening, closing = "{}" if isinstance(value, dict) else "[]"
    body = ",\n".join(inside + line for line in lines)
    return f"{opening}\n{body}\n{'  ' * depth}{closing}"


def main(path: Path = TABLE) -> None:
    """Write units.json, which the scan page reads."""
    path.write_text(laid_out(table()) + "\n", encoding="utf-8")
    print(f"Wrote {path}")


if __name__ == "__main__":
    main(*(Path(arg) for arg in sys.argv[1:2]))
