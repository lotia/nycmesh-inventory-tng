"""Rule 1: which catalogued item an item string names.

The sheet's own lookup is a `VLOOKUP` against the catalogue, and VLOOKUP is
case-insensitive, so a string differing only in case already resolves today and
the rule keeps that. Everything else is a judgement per string, written down
below rather than inferred by a pattern, because the readings have different
consequences and a pattern cannot tell them apart: whether `TP-Link SFP-RJ45`
means the catalogue's `SFP-RJ45 Module` or a typo for its `Tp-Link`, which is a
router, is not something an edit distance decides. Knowing what the things are
does. That argument, and the partition it produces, are
[§1 of the brief](../../../../docs/briefs/sheet-classifiers.md#1-item-string-to-item).

## Unresolvable is an answer

A string resolves to a catalogued item or is recorded here as naming none, and
the second is a real outcome rather than a gap. Three kinds of string get it:

- **Ambiguous.** `mast` names one of `mast straight`, `Mast telescope` and
  `Non-pen Mast`, and `RJ45 couplers` names either coupler. Guessing mints an
  identifier that sends every later scan of that phrase to the wrong shelf.
- **Not in the catalogue at all.** `RCA pole`, a waterproof enclosure, and
  `Matt`, which is somebody's name typed into the item field.
- **The retired SKU scheme.** The `NYCM-ER-LBEG2`-style codes, abandoned in
  2022, whose key is in no tab of the workbook. The few that decode to exactly
  one catalogued item are in `ALIASES` below -- `LBE` is a LiteBeam and the
  catalogue holds one -- and the rest are left alone, because
  `NYCM-ER-SXTSQ` is an SXTsq and the catalogue holds two, so a guess is a
  coin toss over every submission that cites it.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from inventory.sheet.workbook import Sheet

# Item string as written, to the catalogued name it means. Keyed normalised,
# because a variant of a variant (`Omnitik` beside `omnitik`) is the same
# decision twice.
ALIASES = {
    # A TP-Link-branded SFP-to-RJ45 module, not the TP-Link router. See above.
    "tp-link sfp-rj45": "SFP-RJ45 Module",
    # Mikrotik's own SFP-to-RJ45 module, the same thing from the other vendor.
    "mikrotik s-rj01": "SFP-RJ45 Module",
    # The catalogue's `Tp-Link` is the Archer router these name.
    "archer 7": "Tp-Link",
    "archer a7": "Tp-Link",
    "tp link": "Tp-Link",
    "nycm-tp-link-router": "Tp-Link",
    "nycm-tplink": "Tp-Link",
    # `Omni` on its own is left out: the catalogue also holds `Omni DC`.
    "omnitik": "OmniTikPOE",
    "omnitik poe": "OmniTikPOE",
    "nycm-er-momni": "OmniTikPOE",
    # LBE is a LiteBeam whatever follows it, and the catalogue holds one, so
    # the generation and the 5AC do not have to be resolved to answer this.
    "lbe 5ac gen2": "LiteBeam",
    "nycm-er-lbe5a": "LiteBeam",
    "nycm-er-lbeg1": "LiteBeam",
    "nycm-er-lbeg2": "LiteBeam",
    "nycm-er-lbepr": "LiteBeam",
    "j pipe": "Ubiquiti J-Pole",
    "j-pipe": "Ubiquiti J-Pole",
    "ubiquity j-pole": "Ubiquiti J-Pole",
    "w mount": "W-mounts",
    "tough cable": "ToughCable",
    # TOUGHCable Pro is a grade of the same cable; the catalogue does not
    # separate the grades, so it is the one entry it has.
    "tough cable pro": "ToughCable",
    "toughcable connectors": "RJ45 ToughCable connectors",
    "indoor coupler": "RJ-45 Coupler (Indoors)",
    "rj45 indoor couplers": "RJ-45 Coupler (Indoors)",
    "telescopic mast 210-536": "Mast telescope",
    "16 port net power switch": "Netpower 16P",
}

# Item string as written, to why nothing can be done with it. A reason rather
# than a bare set, because the next person to read this needs to know whether
# the string is ambiguous -- which more catalogue rows would fix -- or is not a
# product at all, which nothing will.
UNRESOLVABLE = {
    "omni": "names either OmniTikPOE or Omni DC",
    "mast": "names one of three masts",
    "masts": "names one of three masts",
    "rj45": "names a coupler, a passthrough or a connector",
    "rj45 couplers": "names either the indoor or the outdoor coupler",
    "12 fiber pigtail (1 pack)": "names either the SC/APC or the LC/UPC pigtail",
    "rca pole": "not in the catalogue",
    "large waterproof electric enclosure": "not in the catalogue",
    "matt": "a person's name, typed into the item field",
    # The apostrophe is escaped because it is a right single quotation mark
    # rather than an apostrophe -- a phone put it there, and a lookup keyed on
    # the ASCII one would never match the row this is about.
    "sorry i couldn\u2019t find qr code; big boxes of black utp cable (cable professional)": "prose, not an item",
}

# The retired scheme, whose key is in no tab of the workbook. One reason
# covers all of them, so they are a pattern here rather than one line each
# saying the same thing; the ones that decode to exactly one catalogued item
# are in ALIASES above and are matched before this is reached.
RETIRED_CODE = re.compile(r"^NYCM\b", re.IGNORECASE)
RETIRED_REASON = "a retired NYCM SKU, and the scheme's key is in no tab of the workbook"


class How(StrEnum):
    """The four ways a string can be answered, and the fifth that is a bug.

    Counted apart because they are not equally trustworthy: an exact match
    needs no judgement, and an alias is one somebody made and can be argued
    with. UNACCOUNTED is the one that must never happen -- a string nobody
    resolved and nobody wrote a reason for -- so the report counts it and the
    count is expected to be zero.
    """

    EXACT = "exact"
    CASE = "case"
    ALIAS = "alias"
    UNRESOLVABLE = "unresolvable"
    UNACCOUNTED = "unaccounted"


@dataclass(frozen=True)
class Resolution:
    """What an item string resolved to, how, and -- when it did not -- why."""

    item: str | None
    how: How
    why: str = ""


def _normalised(string: str) -> str:
    """The string as the database compares it.

    `lower()` rather than `casefold()`, and not because either is better: it
    is what `ItemIdentifier.value_normalised` is, a `Lower(Trim())` generated
    column, and
    [data-model.md](../../../../docs/data-model.md#item-itemidentifier-category) says that
    normalisation must not drift between the write path, the importer and the
    scan endpoint. The reader has already trimmed.
    """
    return string.lower()


def resolve(string: str, catalogue: tuple[str, ...]) -> Resolution:
    """The catalogued item this string names, or a reason it names none."""
    if string in catalogue:
        return Resolution(string, How.EXACT)
    normalised = _normalised(string)
    for name in catalogue:
        if _normalised(name) == normalised:
            return Resolution(name, How.CASE)
    if normalised in ALIASES:
        # Only when the catalogue still holds what the alias names. An item
        # renamed there would otherwise resolve to a string that is not in it,
        # and the row would be counted as reaching a catalogued item while the
        # self-check below reported the same alias as pointing at nothing.
        aliased = ALIASES[normalised]
        if aliased in catalogue:
            return Resolution(aliased, How.ALIAS)
        return Resolution(None, How.UNRESOLVABLE, f"aliased to {aliased!r}, which the catalogue no longer holds")
    if normalised in UNRESOLVABLE:
        return Resolution(None, How.UNRESOLVABLE, UNRESOLVABLE[normalised])
    if RETIRED_CODE.match(string):
        return Resolution(None, How.UNRESOLVABLE, RETIRED_REASON)
    return Resolution(None, How.UNACCOUNTED)


def section(sheet: Sheet) -> tuple[str, list[tuple[str, int]]]:
    """The partition over strings, then over the submissions carrying them.

    Both, because they answer different questions: 145 strings for 52 items is
    what the catalogue costs a volunteer, and the submissions are what the
    importer has to place. The last line is a self-check -- an alias naming
    something the catalogue no longer holds resolves to a name that is not
    there, and would otherwise be silent.
    """
    named = [s.item for s in sheet.submissions if s.item]
    strings = set(named)
    resolutions = {string: resolve(string, sheet.catalogue) for string in strings}

    def strings_by(how: How) -> int:
        return sum(1 for r in resolutions.values() if r.how == how)

    return "Item strings", [
        ("distinct strings named", len(strings)),
        ("  matching the catalogue exactly", strings_by(How.EXACT)),
        ("  matching but for case", strings_by(How.CASE)),
        ("  resolved by a hand-written alias", strings_by(How.ALIAS)),
        ("  recorded as naming no catalogued item", strings_by(How.UNRESOLVABLE)),
        ("  neither resolved nor accounted for", strings_by(How.UNACCOUNTED)),
        ("submissions naming an item", len(named)),
        ("  reaching a catalogued item", sum(1 for item in named if resolutions[item].item)),
        ("  reaching nothing", sum(1 for item in named if not resolutions[item].item)),
        ("submissions naming no item at all", len(sheet.submissions) - len(named)),
        ("alias targets the catalogue does not hold", len(set(ALIASES.values()) - set(sheet.catalogue))),
    ]
