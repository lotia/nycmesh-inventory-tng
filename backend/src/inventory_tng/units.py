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
"""

import json
import re
import sys
import unicodedata
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
    """An SI prefix: its symbol, in ASCII, its power of ten, and the other
    spellings it is read by."""

    symbol: str
    exponent: int
    spellings: tuple[str, ...] = ()


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
    Prefix("Q", 30),
    Prefix("R", 27),
    Prefix("Y", 24),
    Prefix("Z", 21),
    Prefix("E", 18),
    Prefix("P", 15),
    Prefix("T", 12),
    Prefix("G", 9),
    Prefix("M", 6),
    Prefix("k", 3),
    Prefix("h", 2),
    Prefix("da", 1),
    Prefix("d", -1),
    Prefix("c", -2),
    Prefix("m", -3),
    Prefix("u", -6, ("\u03bc", "\u00b5")),  # Greek mu, micro sign
    Prefix("n", -9),
    Prefix("p", -12),
    Prefix("f", -15),
    Prefix("a", -18),
    Prefix("z", -21),
    Prefix("y", -24),
    Prefix("r", -27),
    Prefix("q", -30),
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
