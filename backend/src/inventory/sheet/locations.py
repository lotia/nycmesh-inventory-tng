"""Rule 3: the places a note names.

The old sheet had no location concept at all, so people wrote places into the
notes field beside everything else. This recovers them -- but only as
*candidates*. What the real list of stock locations is, is a question for the
people who use the room (`inventory-tng-o5t`), so this offers a seed and says
how much of the ledger it does not cover rather than importing a gazetteer
nobody agreed to.

Two things are settled here rather than in the vocabulary, and
[§3 of the brief](../../../../docs/briefs/sheet-classifiers.md#3-note-to-location)
carries the figures behind both:

- **Case is folded.** Nothing is lost by it: case has never distinguished two
  places in this ledger, and `Sean Mesh Room 131 Broome St` and `sean mesh
  room 131 broome st` are one person writing one room twice. Every pattern
  below is matched case-insensitively.
- **`mesh room` alone is the mesh room.** The narrower readings -- and a
  Broome spelling, or Broome spelled correctly -- answer a question nobody
  asked, and the one note where the phrase is not only about this room,
  `Blue Stockings + mesh room`, names *both* places. That is why a note yields
  a tuple rather than one answer, and why the report counts the notes naming
  more than one. All three readings stay printed, because they are far enough
  apart that a figure quoted without its predicate is not one.
"""

import re
from collections import Counter

from inventory.sheet import Report
from inventory.sheet.workbook import Sheet

# The two halves of the room's own pattern, named because the report prints
# two lines about that one room and they must not come from two alternations
# that can quietly diverge. BROOME covers the five ways the ledger spells the
# street -- broome, broom, beoome, briome, brooke.
MESH_ROOM = re.compile(r"mesh room", re.IGNORECASE)
BROOME = r"\bb[reio]{1,2}o[mk]e?"
SPELLED_BROOME = re.compile(BROOME, re.IGNORECASE)

# Candidate location, to what naming it looks like in a note. Read off the
# export rather than imagined, and deliberately not exhaustive: 444 distinct
# notes, read case-insensitively, name no place, no correction and no job, and
# most of them name an install or an order rather than a place. Being a seed
# for o5t is the design, so the report says what this misses instead of
# guessing at it.
#
# A dict rather than a list of pairs, so that two candidates cannot carry one
# name; the order is the order they are reported in, which dicts keep.
PLACES = {
    "131 Broome": rf"{MESH_ROOM.pattern}|131[\s,.]*{BROOME}",
    "Blue Stockings": r"blue stocking",
    "Mil Mundos": r"mil mundos",
    "Olmsted": r"olmsted",
    "President Street": r"\bpresident\b",
    "Greenwood Cemetery": r"greenwood",
    "Belmont": r"\bbelmont\b",
    "BAM": r"\bbam\b",
    "Astoria": r"\bastoria\b",
    "Columbia": r"\bcolumbia\b",
    "Flatbush Cats": r"flatbush",
    # GSG is the Grand Street Guild, written both ways.
    "Grand Street": r"grand st|\bgsg\b",
    "Boro Park": r"boro park",
    "Harlem": r"\bharlem\b",
    "SN1": r"\bsn1\b",
    "W 171st": r"w\s?171",
    # A volunteer holding stock is a location -- decision 0008 -- and these are
    # how the ledger says so. This one candidate cannot become a Location row
    # as it stands: location_held_by_iff_custody requires a custody location to
    # name its holder, and which volunteer is rule 6's question rather than
    # this one's. It is a marker that custody is meant, for the importer to
    # pair with a person, and o5t should read it as one.
    "a volunteer's home": r"\bapartment\b|home stock|personal stock",
    "basement": r"\bbasement\b",
    "backup shelf": r"backup shelf",
}

NAMED_BY = {name: re.compile(pattern, re.IGNORECASE) for name, pattern in PLACES.items()}


def locations(note: str) -> tuple[str, ...]:
    """Every candidate location this note names, in the order they are listed.

    A tuple rather than one answer because a note can name two: `Blue
    Stockings + mesh room` is the whole reason the widest mesh-room predicate
    is safe to take.
    """
    return tuple(name for name, pattern in NAMED_BY.items() if pattern.search(note))


def section(sheet: Sheet) -> Report:
    """The seed, its coverage, and the three readings of the mesh room.

    The coverage lines are the important ones. A vocabulary that matched
    everything would be a gazetteer somebody invented; this one is a seed, and
    what it leaves is the measure of how far o5t still has to go.
    """
    notes = [s.note for s in sheet.submissions if s.note]
    found = [locations(note) for note in notes]
    named = [places for places in found if places]
    tally = Counter(name for places in named for name in places)
    room = [note for note in notes if MESH_ROOM.search(note)]
    folded = [note.casefold() for note in room]
    return "Locations", [
        ("submissions with a note", len(notes)),
        ("  naming a candidate location", len(named)),
        # Three spaces rather than two, per the depth convention on `Report`:
        # a subset of the line above it, not a third share of the population.
        # At the same indent the children summed to six more than the parent.
        ("   of those, naming more than one", sum(1 for places in named if len(places) > 1)),
        ("  naming none of the vocabulary", len(notes) - len(named)),
        ("distinct candidates named", len(tally)),
        # The seed itself, one line per candidate. This is what o5t takes to
        # the people who use the room, so it is printed rather than described:
        # a list somebody has to reconstruct from the code is not a seed.
        *((f"  {name}", tally[name]) for name in PLACES),
        ("the mesh room, however written", len(room)),
        ("  and 131 or a Broome spelling", sum(1 for note in room if "131" in note or SPELLED_BROOME.search(note))),
        ("  and Broome spelled correctly", sum(1 for note in folded if "broome" in note)),
        # Distinct *notes*, which is what the brief has always counted and is
        # not the same as distinct spellings of the room: two notes naming the
        # room identically and differing after it are two. The pair is here
        # for the case decision, and what it shows is the collapse.
        ("notes naming it, read literally", len(set(room))),
        ("notes naming it, read case-insensitively", len(set(folded))),
    ]
