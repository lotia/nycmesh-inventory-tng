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

An item's or a package's name is stored as typed on its form, its units
respelt by their symbols only where a reading rule says so (spell, Ali,
2026-11-15): case is read loosely only where one reading is a likely
size, and the form asks where two are.

A reading rule (models.ReadingRule, inventory-tng-c0c.4) settles how a
unit as typed after a number (`M`, `G`) is read, or that it is no unit:
looked up before the table, wherever a unit after a number is read, by
search here and on the scan page, and by the forms, always first. Where
none is and a unit may be several kinds, the words near it in a name
decide (CLUES), then the profile (PROFILE, LIKELY), on both sides alike
(Ali, 2026-11-15, inventory-tng-c0c.3).
"""

import json
import re
import sys
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from babel.support import LazyProxy

from inventory_tng.local_env import CLONE
from inventory_tng.phrases import gettext, lazy_gettext

# Raise it whenever a change to the table reads any text otherwise, or
# search keeps it otherwise (models.held): rows record what folded them
# (models.FOLDED_WITH), so those folded before can be folded again, and a
# test fails until it is raised. 2: each field kept as typed too, and "in"
# no unit (Ali, 2026-11-15). 3: search reads units as the forms do, by
# the words near them, the profile and products (Ali, 2026-11-15,
# inventory-tng-c0c.3). 4: search reads VDC and VAC as volts, and a
# number before a unit without its thousands separators (Ali, 2026-11-15,
# inventory-tng-ist.1). 5: search reads a comma after a lone nought as a
# decimal point, `0,500 m` as `0.500 m` (Ali, 2026-11-15, inventory-tng-4i1).
VERSION = 5

# The most groups (groups) a search is matched by, each alone; one with more
# is matched whole only (Ali, 2026-11-15, inventory-tng-ist.1).
GROUPS_MAX = 8

TABLE = CLONE / "frontend" / "src" / "units.json"


@dataclass(frozen=True)
class Kind:
    """A kind of quantity: the unit its sizes are measured in, a size that
    is plausible for this inventory, low to high, in that unit, and the
    kind in words, as the pages say it."""

    base: str
    plausible: tuple[float, float]
    words: LazyProxy


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
    one, and are read by search and to space a name as shown
    (shown_name), never to spell one (Ali, 2026-11-15). A size in it is
    `factor` times the size (its prefix's power `power` times, as for m3)
    + `offset` in its kind's base; a level in decibels has no factor."""

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


# Ali, 2026-11-12: this inventory's kinds, each with one plausible size
# and its words, as the pages say it.
KINDS: dict[str, Kind] = {
    "length": Kind("m", (1e-4, 1e5), lazy_gettext("length")),
    "frequency": Kind("Hz", (1.0, 1e12), lazy_gettext("frequency")),
    "data rate": Kind("bit/s", (1e3, 1e12), lazy_gettext("data rate")),
    "power": Kind("W", (1e-6, 1e5), lazy_gettext("power")),
    # Power as a level, and an antenna's gain, both in decibels.
    "power level": Kind("dBm", (-120.0, 60.0), lazy_gettext("power level")),
    "gain": Kind("dBi", (-10.0, 50.0), lazy_gettext("gain")),
    "voltage": Kind("V", (1e-3, 1e5), lazy_gettext("voltage")),
    # Translators: electric current, a kind of unit.
    "current": Kind("A", (1e-6, 1e3), lazy_gettext("current")),
    "temperature": Kind("degC", (-100.0, 1000.0), lazy_gettext("temperature")),
    "capacitance": Kind("F", (1e-15, 1e3), lazy_gettext("capacitance")),
    "resistance": Kind("Ohm", (1e-3, 1e9), lazy_gettext("resistance")),
    "mass": Kind("g", (1e-3, 1e7), lazy_gettext("mass")),
    "volume": Kind("L", (1e-3, 1e4), lazy_gettext("volume")),
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
    # VDC and VAC, as search reads them only (Ali, 2026-11-15).
    Unit("V", "voltage", True, ("V",), (), ("volt", "volts", "VDC", "VAC")),
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

# Each unit's name, given its prefix's (or ""), in US spelling, as the app
# follows US conventions (Ali, 2026-11-15, inventory-tng-g6u); and its
# symbol in a name where that is not its symbol here: as the SI Brochure
# writes it, in the spelling models.canonical keeps (docs/decisions/0021,
# 2026-11-14).
NAMES: dict[str, Callable[[str], str]] = {
    # Translators: prefix is an SI prefix in English, such as kilo, or none.
    "m": lambda prefix: gettext("%(prefix)smeters", prefix=prefix),
    "[in_i]": lambda _: gettext("inches"),
    "ft": lambda _: gettext("feet"),
    "yd": lambda _: gettext("yards"),
    "mi": lambda _: gettext("miles"),
    # Translators: prefix is an SI prefix in English, such as kilo, or none.
    "Hz": lambda prefix: gettext("%(prefix)shertz", prefix=prefix),
    # Translators: prefix is an SI prefix in English, such as kilo, or none.
    "bit/s": lambda prefix: gettext("%(prefix)sbits per second", prefix=prefix),
    # Translators: prefix is an SI prefix in English, such as kilo, or none.
    "W": lambda prefix: gettext("%(prefix)swatts", prefix=prefix),
    "dBm": lambda _: gettext("decibel-milliwatts"),
    "dBi": lambda _: gettext("decibels isotropic"),
    # Translators: prefix is an SI prefix in English, such as kilo, or none.
    "V": lambda prefix: gettext("%(prefix)svolts", prefix=prefix),
    # Translators: prefix is an SI prefix in English, such as kilo, or none.
    "A": lambda prefix: gettext("%(prefix)samperes", prefix=prefix),
    "degC": lambda _: gettext("degrees Celsius"),
    "degF": lambda _: gettext("degrees Fahrenheit"),
    # Translators: prefix is an SI prefix in English, such as kilo, or none.
    "K": lambda prefix: gettext("%(prefix)skelvins", prefix=prefix),
    # Translators: prefix is an SI prefix in English, such as kilo, or none.
    "F": lambda prefix: gettext("%(prefix)sfarads", prefix=prefix),
    # Translators: prefix is an SI prefix in English, such as kilo, or none.
    "Ohm": lambda prefix: gettext("%(prefix)sohms", prefix=prefix),
    # Translators: prefix is an SI prefix in English, such as kilo, or none.
    "g": lambda prefix: gettext("%(prefix)sgrams", prefix=prefix),
    "t": lambda _: gettext("metric tons"),
    "lb": lambda _: gettext("pounds"),
    "oz": lambda _: gettext("ounces"),
    # Translators: prefix is an SI prefix in English, such as kilo, or none.
    "L": lambda prefix: gettext("%(prefix)sliters", prefix=prefix),
    # Translators: prefix is an SI prefix in English, such as kilo, or none.
    "m3": lambda prefix: gettext("cubic %(prefix)smeters", prefix=prefix),
    "gal": lambda _: gettext("US gallons"),
}
# Data rates as printed: bps, Mbps, Gbps (Ali, 2026-11-15,
# inventory-tng-c0c.3).
SHOWN: dict[str, str] = {
    "degC": "°C",
    "degF": "°F",
    "Ohm": "\u03a9",
    "m3": "m\u00b3",
    "bit/s": "bps",
}


def table() -> dict[str, Any]:
    """What reading a unit needs, as the scan page reads it (units.json):
    each prefix's spellings, the symbol first, and power of ten; each
    unit's spellings, in the order they are tried, its kind and how a size
    in it is one in its kind's base; each kind's plausible sizes; and the
    profile, products and clues that decide a reading."""
    return {
        "version": VERSION,
        "prefixes": [
            {
                "spellings": [prefix.symbol, *prefix.spellings],
                "exponent": prefix.exponent,
            }
            for prefix in PREFIXES
        ],
        "units": [
            {
                "symbol": unit.symbol,
                "prefixed": list(unit.takes_prefix()),
                "alone": list(unit.alone()),
                "symbols": list(unit.prefixed()),
                "kind": unit.kind,
                "factor": unit.factor,
                "offset": unit.offset,
                "power": unit.power,
            }
            for unit in UNITS
        ],
        "kinds": {kind: list(each.plausible) for kind, each in KINDS.items()},
        "profile": list(PROFILE),
        "likely": sorted(LIKELY),
        "bare_rates": BARE_RATES,
        "asked": sorted(ASKED),
        "products": sorted(PRODUCTS),
        "clues": {kind: sorted(words) for kind, words in CLUES.items()},
        "groups_max": GROUPS_MAX,
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
        """In words: `microfarads`, `cubic kilometers`."""
        return NAMES[self.unit.symbol](self.prefix.name if self.prefix else "")

    @property
    def called(self) -> str:
        """What a form's question calls it, as Ali worded it (2026-11-15,
        inventory-tng-c0c.3): its unit in words, `meters`, but a data rate so,
        `3Mbps · data rate`."""
        return self.kind_words if self.unit.kind == "data rate" else self.named

    @property
    def kind_words(self) -> str:
        """Its kind, in words: `data rate`."""
        return str(KINDS[self.unit.kind].words)

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


def rule_key(token: str) -> str:
    """What a reading rule is kept by: a unit as typed after a number, case
    kept, in canonical spelling (NFC), so one rule reads `10G`, `1G` and
    `40 G` (Ali, 2026-11-15, inventory-tng-c0c.3), and `G` never settles
    `g`."""
    return unicodedata.normalize("NFC", token)


def _number(text: str, end: int) -> str:
    """The number ending at `end` in `text`, as typed."""
    number = _NUMBER.search(text[:end])
    assert number is not None  # It ends at a digit AFTER_A_NUMBER found.
    return number[0]


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


# A number grouped in thousands by commas or by spaces, a thin one
# decomposed being one, ending at its last digit: `1,000`, `1 000`, not
# starting within another so grouped (`345 678` in `12 345 678`), and by
# commas after a number and a space (`6 1,000`), nor by commas after a lone
# nought (_NOUGHT). Search keeps it, before a unit it reads, without them
# (Ali, 2026-11-15, inventory-tng-ist.1): frontend/src/units.ts's,
# character for character.
_GROUPED = re.compile(
    r"(?<![^\W_])(?:(?<![0-9][.,])(?!0,)[0-9]{1,3}(?:,[0-9]{3})+"
    r"|(?<![0-9][., ])[0-9]{1,3}(?: [0-9]{3})+)(?:[.][0-9]+)?\Z"
)
# A lone nought, then a comma and digits, ending at its last digit, with
# its minus as _NUMBER's: the comma is a decimal point, so search keeps
# `0,500` as `0.500` and a form judges it so (Ali, 2026-11-15,
# inventory-tng-4i1): frontend/src/units.ts's, character for character.
_NOUGHT = re.compile(r"(?:(?<![^\W_])[-\u2212])?(?<![^\W_])(?<![0-9][.,])0,[0-9]+\Z")


def _searched(text: str, end: int) -> tuple[re.Match[str], str] | None:
    """The number ending at `end` in `text` where search reads it otherwise
    than as typed, and how it reads it: a lone nought's comma as a point
    (_NOUGHT), or thousands (_GROUPED) without their separators, unless a
    product's number starts them (`Cat 6 305 m` is a product's number, then
    another). None: read as typed."""
    if nought := _NOUGHT.search(text, 0, end):
        return nought, nought[0].replace(",", ".")
    grouped = _GROUPED.search(text, 0, end)
    if grouped is None or _product(text, grouped.start()):
        return None
    return grouped, re.sub("[, ]", "", grouped[0])


# Where respell changes a text, and what to: a start, an end and the spelling.
type Edit = tuple[int, int, str]


def _after_a_number(text: str) -> Iterator[re.Match[str]]:
    """Each match of AFTER_A_NUMBER in `text` that may be a unit: none
    beside a combining mark (_marked) or in a product's name (_product)."""
    for match in AFTER_A_NUMBER.finditer(text):
        if not _marked(text, match.end()) and not _product(text, match.end(1) - 1):
            yield match


def _edits(text: str, rules: Rules | None) -> list[Edit]:
    """What respell changes in `text`, in order: each unit read right after
    a number, from its number, and each unit alone spelt with a sign no word
    is."""
    edits: list[Edit] = []
    for match in _after_a_number(text):
        grouped, number = _searched(text, match.end(1)) or (None, None)
        found = _found(text, match, rules, number)
        if found is not None and (found.read or found.rule):
            # Read, or no unit by its rule.
            reading = found.read
        else:
            reading = read(match[2])
        if reading is not None:
            start = grouped.start() if grouped else match.start(1)
            edits.append((start, match.end(), f"{number or match[1]}{reading}"))
    for match in ALONE.finditer(text):
        if match[1].isascii() or _marked(text, match.start() - 1, match.end()):
            continue
        if any(s < match.end() and match.start() < e for s, e, _ in edits):
            continue
        if (reading := read(match[1])) is not None:
            edits.append((match.start(), match.end(), str(reading)))
    return sorted(edits)


def _applied(text: str, edits: list[Edit], start: int = 0, end: int = -1) -> str:
    """`text` from `start` to `end` (its end, if -1) with each of `edits`
    within made."""
    end = len(text) if end == -1 else end
    parts, at = [], start
    for first, last, spelling in edits:
        if start <= first and last <= end:
            parts += [text[at:first], spelling]
            at = last
    return "".join([*parts, text[at:end]])


def respell(text: str, rules: Rules | None = None) -> str:
    """`text` with each unit read right after a number spelt by its symbol
    (str(Reading)), joined to the number, the number without thousands
    separators and a comma after a lone nought a point, as the forms read
    it (_found: its reading rule in `rules`, the words near it, the
    profile), else as the table reads it, case first, and each unit alone
    spelt with a sign no word is (not ASCII) by its symbol; a product's
    name (PRODUCTS) as it is. `text` is decomposed (NFKD), as both the
    server and the scan page fold it, so `℃` is `°C`, `µ` `μ` and a thin
    space a space."""
    return _applied(text, _edits(text, rules))


# A word of a search, between spaces, control characters and U+FEFF,
# which Python's and JavaScript's \s each take for a space or not
# differently: frontend/src/units.ts's SEARCH_WORD.
_SEARCH_WORD = re.compile(r"[^\s\x00-\x1f\x7f-\x9f\ufeff]+")


def groups(text: str, rules: Rules | None = None) -> list[tuple[str, str]]:
    """What a search for `text` is matched by group by group (Ali,
    2026-11-15, inventory-tng-ist.1): each number with the unit respell
    reads right after it, its space kept, and every other word alone, each
    as typed and respelt, cut from respelling the whole of `text`, so rules,
    products and the words near a unit read as they do there (`spool 305M`
    is `305m`). Empty if there are more than GROUPS_MAX. `text` is
    decomposed (NFKD), as respell's."""
    edits = _edits(text, rules)
    spans: list[list[int]] = []
    for word in _SEARCH_WORD.finditer(text):
        # Joined to the word before where respell reads across the space.
        if spans and any(s < spans[-1][1] and word.start() < e for s, e, _ in edits):
            spans[-1][1] = word.end()
        else:
            spans.append([word.start(), word.end()])
    if len(spans) > GROUPS_MAX:
        return []
    return [(text[a:b], _applied(text, edits, a, b)) for a, b in spans]


# How a name an administrator types is read (docs/decisions/0021, Ali,
# 2026-11-15, inventory-tng-c0c.3): saved as typed, each unit
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
# A bare G, M or K after a number may be a data rate: 10G is 10 Gbps; and
# so may a bare g (Ali, 2026-11-15).
BARE_RATES = {"G": "G", "M": "M", "K": "k", "g": "G"}
# Units the profile never settles alone where several readings are
# plausible: a g after a number is grams as spelt and a data rate as often
# in this inventory's names, so never silently grams (Ali, 2026-11-15).
# Its rule decides, else the words near it, else the form asks.
ASKED = frozenset(("g",))
# The kinds a network inventory names most (Ali, 2026-11-15): one of them
# among a unit's plausible readings is taken, as 10G is 10 Gbps, not 10
# grams, and 4 K 4 kbps; two, as 3M may be 3 Mbps or 3 m, are left to its
# rule, the words near it or the administrator.
LIKELY = frozenset(PROFILE[:2])
# Words that, in a name, say what kind a unit after a number is, where it
# may be several and no rule says (Ali, 2026-11-15): "3M" near "cable" or "Cat6" is a
# length, near "switch" or "SFP" a data rate. Compared in lower case.
CLUES: dict[str, frozenset[str]] = {
    "data rate": frozenset(
        (
            "sfp",
            "sfp+",
            "qsfp",
            "switch",
            "uplink",
            "port",
            "ports",
            "ethernet",
            "router",
            "transceiver",
            "nic",
            "lan",
            "wan",
            "link",
            "gbe",
            "fiber",
            "fibre",
            "backhaul",
        )
    ),
    "length": frozenset(
        (
            "spool",
            "cable",
            "cord",
            "wire",
            "reel",
            "roll",
            "drop",
            "patch",
            "conduit",
            "jumper",
            "pigtail",
            "rope",
            "cat5",
            "cat5e",
            "cat6",
            "cat6a",
            "cat7",
            "cat8",
        )
    ),
    "gain": frozenset(("antenna", "dish", "sector", "omni", "horn")),
    "voltage": frozenset(("battery", "psu", "supply", "adapter", "injector")),
    "mass": frozenset(
        (
            "weight",
            "weighs",
            "paste",
            "grease",
            "compound",
            "sealant",
            "epoxy",
            "lubricant",
        )
    ),
}
# A word, as CLUES has them; a part of one between hyphens, as PRODUCTS.
_PART = re.compile(r"[A-Za-z0-9+]+")
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
# A word, as PRODUCTS has them: letters, digits, + and - within (a part
# of one between hyphens is a _PART); and the word or part right before one.
_WORD = re.compile(r"[A-Za-z0-9+]+(?:-[A-Za-z0-9+]+)*")
_BEFORE = re.compile(r"([A-Za-z0-9+]+)[ -]?\Z")


def reading_of(symbol: str) -> Reading | None:
    """The reading spelt `symbol` (str(Reading)), if one is."""
    reading = read(symbol)
    return reading if reading is not None and str(reading) == symbol else None


def readings(token: str) -> list[tuple[Reading, bool]]:
    """Every unit `token` spells in any case, each once, a bare G, M or K
    as a data rate too (BARE_RATES), and whether it is spelt so as typed:
    by PROFILE's kinds, those spelt as typed first in each, as a form
    offers them (`10Gbps · data rate`, then `10g · grams`). `MF`
    is megafarads as typed and millifarads otherwise."""
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
    return sorted(found, key=lambda pair: (_rank(pair[0]), not pair[1]))


def _rank(reading: Reading) -> int:
    return PROFILE.index(reading.unit.kind)


def _product(name: str, at: int) -> bool:
    """Whether the word the digit at `at` is in (the last of a number
    AFTER_A_NUMBER found, or the first of one grouped in thousands), or its
    part between hyphens, alone or joined to the word or part before it, is
    a product's (PRODUCTS): `Cat6a`, `Cat 6a`, `Cat-6a`, `Cat6a-patch`,
    `5AC`."""
    word = next(w for w in _WORD.finditer(name) if w.start() <= at < w.end())
    part = next(p for p in _PART.finditer(name) if p.start() <= at < p.end())
    said = [
        (before[1] if (before := _BEFORE.search(name[: each.start()])) else "")
        + each[0]
        for each in (word, part)
    ]
    return any(s.lower() in PRODUCTS for s in (word[0], part[0], *said))


@dataclass(frozen=True)
class Found:
    """What may be a unit right after a number in a name: the number as
    typed, the unit as typed (`token`), where the space before it, if any,
    and it are (`start`, `end`), and the readings of it that are plausible
    sizes (Reading.plausible), the one spelt as typed first, then by
    PROFILE. `read` is what it reads as (_found): by its rule (`rule`;
    None: no unit), else by the words near it (`clue`, those words), else
    the one likely."""

    number: str
    token: str
    start: int
    end: int
    plausible: tuple[Reading, ...]
    read: Reading | None
    rule: Rule | None = None
    clue: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        """What its reading rule is kept by (rule_key)."""
        return rule_key(self.token)

    @property
    def typed(self) -> str:
        """The number and its unit as typed, joined: `3M`."""
        return self.number + self.token

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
    and each a reading rule names, but in a product's name (PRODUCTS), each
    read as _found says. `name` is in canonical spelling (models.canonical),
    so `℃` is `°C`."""
    return [
        found
        for match in _after_a_number(name)
        if (found := _found(name, match, rules)) is not None
    ]


def _found(
    text: str, match: re.Match[str], rules: Rules | None, number: str | None = None
) -> Found | None:
    """The unit AFTER_A_NUMBER found in `text`, as read (Ali, 2026-11-15,
    inventory-tng-c0c.3): by its reading rule, which always wins; else, where
    several readings are plausible, by the words near it (CLUES), if they say
    one kind; else by the one plausible reading, the one of a LIKELY kind, or
    the one spelt as typed where none is likely. None if it is no unit's
    spelling and no rule's. Its number is as typed, or `number` where search
    reads it so (respell: `1 000` as `1000`)."""
    number, token = number or _number(text, match.end(1)), match[2]
    rule = (rules or {}).get(rule_key(token))
    every = readings(token)
    if rule is None and not every:
        return None
    # A comma after a lone nought is a decimal point (_NOUGHT), else groups;
    # respell's `number` has none left to judge.
    nought = "," in number and _NOUGHT.search(text, 0, match.end(1))
    point = "." if nought else ""
    value = float(number.replace(",", point).replace("\u2212", "-"))
    plausible = [(r, exact) for r, exact in every if r.plausible(value)]
    readable = tuple(r for r, _ in plausible)
    words: tuple[str, ...] = ()
    if rule is not None:
        settled = None if rule.reading is None else reading_of(rule.reading)
    elif len(readable) > 1 and (clue := _clue(text, readable)) is not None:
        settled, words = clue
    else:
        asked = token in ASKED and len(plausible) > 1
        settled = None if asked else _likeliest(plausible)
    return Found(
        number, token, match.end(1), match.end(), readable, settled, rule, words
    )


def _clue(
    text: str, readable: tuple[Reading, ...]
) -> tuple[Reading, tuple[str, ...]] | None:
    """The reading the words in `text` say (CLUES), if they say one kind,
    and those words."""
    words = {word.lower() for word in _PART.findall(text)}
    said = [
        (r, tuple(sorted(words & CLUES.get(r.unit.kind, frozenset()))))
        for r in readable
    ]
    most = max(len(clues) for _, clues in said)
    best = [pair for pair in said if len(pair[1]) == most]
    return best[0] if most and len(best) == 1 else None


def _likeliest(plausible: list[tuple[Reading, bool]]) -> Reading | None:
    """The one plausible reading; else the one of a LIKELY kind; else, none
    being likely, the one spelt as typed. None if two are as likely."""
    if len(plausible) == 1:
        return plausible[0][0]
    likely = [r for r, _ in plausible if r.unit.kind in LIKELY]
    if len(likely) == 1:
        return likely[0]
    exact = [r for r, spelt in plausible if spelt]
    return exact[0] if not likely and len(exact) == 1 else None


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
    return _applied(name, [(f.start, f.end, s) for f, s in spelt.items()])


# Between a number and its unit in a name as shown, and between thousands
# in a number of five digits or more, as the SI Brochure writes them (Ali,
# 2026-11-15, inventory-tng-ist.3): neither breaks a line.
NO_BREAK_SPACE = "\u00a0"
THIN_NO_BREAK_SPACE = "\u202f"
# A number as a name may type it before a unit: a sign, its whole part, plain
# or grouped by commas in thousands as US conventions write them, and any
# decimal part. Another use of commas (`1,2,3`, `0,500`) is left as typed.
_SHOWN_NUMBER = re.compile(
    r"([-\u2212]?)([1-9][0-9]{0,2}(?:,[0-9]{3})+|[0-9]+)([.][0-9]+)?"
)


def _shown_number(number: str) -> str:
    """`number` without its thousands separators, and grouped by thin
    no-break spaces from five digits (`10,000` is `10 000`, `1,000`
    `1000`); as typed if it is not plainly a number (_SHOWN_NUMBER)."""
    parts = _SHOWN_NUMBER.fullmatch(number)
    if parts is None:
        return number
    sign, whole, fraction = parts[1], parts[2].replace(",", ""), parts[3] or ""
    if len(whole) >= 5:
        head = len(whole) % 3 or 3
        thousands = [whole[i : i + 3] for i in range(head, len(whole), 3)]
        whole = THIN_NO_BREAK_SPACE.join([whole[:head], *thousands])
    return sign + whole + fraction


# What, right before a number, makes it part of a word or a code, such as
# a part number (`UF-SM-10G`, `ES-8-150W`): left as typed. And what, right
# after its unit, runs it on into one (`10G-SFP+`, `48V-to-12V`,
# `10GHz/20 MHz`).
_IN_A_CODE = re.compile(r"[\w/.+-]")
_INTO_A_CODE = re.compile(r"[/-]")
# A sign alone right before a number, as _NUMBER takes it.
_SIGN = re.compile(r"(?<![^\W_])[-\u2212]\Z")


def shown_name(name: str, rules: Rules | None = None) -> str:
    """`name` as the app shows it, wherever a name is read (Ali, 2026-11-15,
    inventory-tng-ist.3): each number a unit is read right after (_found:
    its rule, the words near it, the profile; never a product's), or a
    unit's word as search reads it (inventory-tng-tze), without its
    thousands separators, grouped from five digits (_shown_number), and a
    no-break space between it and its unit, spelt as typed: `Cap 10uF 50V`
    is `Cap 10 uF 50 V`, `Box 10,000 feet` `Box 10 000 feet`. The rest, a
    unit not read among it, and a number within a word or a code
    (_IN_A_CODE, _INTO_A_CODE), as typed. A number grouped by spaces
    (_GROUPED, any space search reads as one), with its sign, is read as
    search reads it, one number, but shown whole and as typed, as it may be
    a pack count and a size: `Cable 2 100 ft` is `Cable 2 100 ft`, never
    `2100 ft`, its ordinary spaces no-break so it never breaks across a
    line (Ali, 2026-11-16, inventory-tng-t78). `name` is as saved
    (models.canonical, spell)."""
    # Any space as search reads it, a plain one (NFKD), so `1\u202f000` is
    # grouped too; one character for one, so where each is stays.
    plain = "".join(" " if unicodedata.category(c) == "Zs" else c for c in name)
    edits = []
    for match in _after_a_number(name):
        token = match[2]
        searched: str | None = None
        if (hit := _searched(plain, match.end(1))) and " " in hit[0][0]:
            grouped, joined = hit
            sign = _SIGN.search(name, 0, grouped.start())
            start = grouped.start() if sign is None else sign.start()
            # Its ordinary spaces no-break, the rest as typed.
            shown = name[start : match.end(1)].replace(" ", NO_BREAK_SPACE)
            searched = name[start : grouped.start()] + joined
        else:
            number = _number(name, match.end(1))
            start, shown = match.end(1) - len(number), _shown_number(number)
        if _IN_A_CODE.match(name[start - 1 : start]) or _INTO_A_CODE.match(
            name, match.end()
        ):
            continue
        found = _found(name, match, rules, searched)
        # A word no form reads, as _edits reads it.
        if (read(token) if found is None else found.read) is None:
            continue
        edits.append((start, match.end(), shown + NO_BREAK_SPACE + token))
    return _applied(name, edits)


def main(path: Path = TABLE) -> None:
    """Write units.json, which the scan page reads; the task then lays it
    out as Biome does."""
    text = json.dumps(table(), indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")
    print(f"Wrote {path}")


if __name__ == "__main__":
    main(*(Path(arg) for arg in sys.argv[1:2]))
