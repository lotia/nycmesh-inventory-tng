"""Rule 6: which volunteer a submission is from.

The form asked for a name as free text on every submission, and for an address
on none of them. So the ledger's people are a pile of spellings, a minority of
them beside an address. Neither field is a key on its own, and the second is
the one that surprises:

- **A name is not one person.** Two volunteers really are both called Sean,
  the model says so on `Volunteer.Meta.ordering`, and a first name typed on a
  phone is all most submissions carry.
- **An address is not one person either.** A volunteer with their hands full
  asks whoever is holding a phone to submit for them, so one address carries
  several people's work; and one volunteer signs in from a personal address, a
  mesh address, and misspellings of both, so one person carries several
  addresses. Keying on the address is therefore no safer than keying on the
  name -- and *unioning spellings that share an address* is worse than either,
  because it is transitive: the address somebody lends the most pulls
  everybody it ever carried into one volunteer.

## The rule

A submission is from the volunteer its **name** names, folded to lower case.
Case has never distinguished two people in this ledger, and the form's own
free-text field is where the variation comes from.

Two folded spellings are **one volunteer** when the workbook itself shows they
are, which takes *both* halves of a test: **the same name**, meaning one
spelling is the other written longer -- a surname or an initial added -- or
differs from it by a single character once spaces are taken out; and **the
same person**, meaning the two spellings appear beside a common address.

Neither half alone will do. The first alone merges a first name into every
longer name starting with it, which is how two volunteers who share one become
one row. The second alone is the transitive merge above. Together they are
narrow enough to state as a rule rather than to write out as a table of who
is who, which matters here beyond tidiness: the workbook is not ours to
publish, and a table of its people is the part of it that must least be.

Where the name field holds no name, the **address** stands in, and only where
the workbook says whom it belongs to: an address beside which exactly one
volunteer is ever named is that volunteer's. An address no name is ever
written beside is a volunteer of its own -- somebody moved that hardware, and
a row an administrator can put a name to keeps the movement attributed. An
address written beside *more than one* volunteer names nobody, because minting
a row for it would mint a duplicate of somebody already here.

## Separate rows beat wrong merges

A duplicate that survives is a `Volunteer` row an administrator merges later:
`merged_into` is set, the duplicate stops being offered, and the ledger is not
touched, so the work stays attributed
([data-model.md](../../../../docs/data-model.md#volunteer)). A wrong merge is
not the mirror of that. It is written into every row imported against it, and
the ledger is append-only, so undoing it is not an edit anybody can make.

The rule therefore errs toward separate rows, and **says which ones it is
unsure of** rather than deciding for the administrator. A volunteer that never
gave an address, and whose name is the same as another volunteer's under the
first half of the test above, is its own row and is **flagged** -- generously,
so a short name one letter from another short name is flagged even where the
two are ordinary separate names. Why generosity is the right side to err on,
and what the flags cost, is
[§6 of the brief](../../../../docs/briefs/sheet-classifiers.md#6-person-to-volunteer).

## What is not a name

The name field also collects things that are not names, and each is a
judgement rather than a pattern, so they are written down in `NOT_A_NAME`
below with the reason. An address typed into it is the one that is a rule
rather than an entry: a field with an `@` in it is an address, and it is read
as one, which reaches the volunteer through the fallback above rather than
minting a volunteer whose name is their email.

The figures, and what they say about how many people this ledger actually has,
are [§6 of the brief](../../../../docs/briefs/sheet-classifiers.md#6-person-to-volunteer).
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations

from inventory.sheet import Report
from inventory.sheet.workbook import Sheet, Submission

# What the name field held, to why it is not a name. A reason rather than a
# bare set, for the reason items.py gives about its own: the next reader needs
# to know whether the row can be attributed some other way.
NOT_A_NAME = {
    "5.0": "a quantity typed into the name field",
    "y": "a single letter, which names nobody",
    "testing": "a test submission",
    "update inventory": "a note typed into the name field",
    "partial box, 4 individual couplers": "a note typed into the name field",
}


class How(StrEnum):
    """How a submission reached its volunteer, or why it reached none."""

    NAME = "name"
    ADDRESS = "address"
    NOBODY = "nobody"


@dataclass(frozen=True)
class Person:
    """The volunteer a submission is from, and how the rule got there.

    `key` is stable across the whole export and is what `inventory-tng-a82`
    chains submissions on: a folded name spelling for a volunteer the workbook
    names, and an address for one it does not.
    """

    key: str | None
    how: How
    why: str = ""


def spelled(name: str) -> str:
    """The name field as a volunteer key, or "" where it holds no name."""
    folded = name.lower()
    if not folded or "@" in folded or folded in NOT_A_NAME:
        return ""
    return folded


def addressed(email: str) -> str:
    """The address field as a volunteer key, or "" where it holds none.

    The other half of `spelled`. Named for the same reason: this module owns
    who a submission is from, and rule 5 keys on the answer too -- so a fold
    written inline in both places is two modules quietly deciding separately
    whether two addresses are one address.

    `lower` rather than `casefold`, matching `spelled`, and NOT because either
    matches a database column. NEITHER KEY HAS ONE. `Volunteer.email` and
    `Volunteer.sheet_key` carry plain partial unique constraints on the bare
    column -- case-sensitive, nothing generated in between -- so this fold is
    Python's alone and there is no counterpart for it to drift from.

    It said otherwise until `inventory-tng-xvp1`, naming a column on the
    identifier tables that these keys never touch.

    The reason for `lower` stands without the parity. It is the fold this
    codebase answers "are these two strings one" with wherever it asks --
    `identifiers.Canonical` ends in SQL's `lower` and `identifiers.normalised`
    in Python's -- and `casefold` is a third answer again, mapping the German
    ss where neither of the others does.
    """
    return email.lower()


def _one_character_apart(one: str, other: str) -> bool:
    """One insertion, deletion or substitution between them."""
    if abs(len(one) - len(other)) > 1:
        return False
    short, long = sorted((one, other), key=len)
    same = 0
    while same < len(short) and short[same] == long[same]:
        same += 1
    if len(short) == len(long):
        return same < len(short) and short[same + 1 :] == long[same + 1 :]
    return short[same:] == long[same + 1 :]


def near(one: str, other: str) -> str:
    """Why these two spellings might be one person's name, or "" where they might not.

    Spaces come out first, so that a name written `John B` and the same name
    written `JohnB` are not two people on the strength of a space bar.
    """
    a, b = one.replace(" ", ""), other.replace(" ", "")
    if a == b:
        return "the same name but for the spaces in it"
    if a.startswith(b) or b.startswith(a):
        return "the same name written longer"
    if _one_character_apart(a, b):
        return "the same name but for one character"
    return ""


def busiest(among: Iterable[str], counted: Mapping[str, int]) -> str:
    """The commonest of these strings, ties broken alphabetically.

    The one tie-break this package picks a spelling by, named so that every
    place that has to pick one -- the key a volunteer is known by, the name an
    administrator is shown, the address that is theirs -- uses it rather than
    writing it out again and being free to answer differently.
    """
    return min(among, key=lambda one: (-counted[one], one))


def _spoken_for(written: Mapping[str, int], joined: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Each spelling mapped to the one that speaks for its group.

    The group's busiest spelling, so a volunteer's key is a spelling somebody
    actually wrote rather than one this invents.
    """
    parent = {spelling: spelling for spelling in written}

    def root(spelling: str) -> str:
        while parent[spelling] != spelling:
            parent[spelling] = parent[parent[spelling]]
            spelling = parent[spelling]
        return spelling

    for one, other in joined:
        parent[root(one)] = root(other)
    groups: dict[str, list[str]] = defaultdict(list)
    for spelling in written:
        groups[root(spelling)].append(spelling)
    spoken: dict[str, str] = {}
    for group in groups.values():
        spoken.update(dict.fromkeys(group, busiest(group, written)))
    return spoken


@dataclass(frozen=True)
class Directory:
    """Who the workbook names, worked out from every submission at once.

    A directory rather than a function of one row, because the address
    fallback and the flags are both questions about the export as a whole:
    whether an address names one volunteer or several cannot be answered from
    the row holding it.
    """

    # Folded name spelling to the volunteer it belongs to.
    by_name: Mapping[str, str]
    # Folded name spelling to a spelling somebody actually typed. The fold is
    # a good key and a poor thing to show an administrator, so the rule that
    # picks the key picks the display name too rather than the importer
    # picking a second one beside it.
    spellings: Mapping[str, str]
    # Address to the volunteer it reaches, for the addresses that reach one.
    by_address: Mapping[str, str]
    # A volunteer this rule is unsure of, to the volunteers it might be the
    # same person as. Empty where it is an address nobody was named beside.
    flagged: Mapping[str, tuple[str, ...]]
    # Addresses each volunteer wrote. Kept because the two ways an address
    # count misleads -- a volunteer who wrote none, and one who wrote several
    # -- are what make the headcount a range, and the report has to show both.
    held: Mapping[str, frozenset[str]]
    # Addresses more than one volunteer was named beside, to the volunteers
    # they reach. These are why keying on the address is no safer than keying
    # on the name, so the report counts them rather than the brief asserting
    # it: a volunteer with full hands hands their phone over.
    shared: Mapping[str, frozenset[str]]
    # Submissions behind each volunteer, so that a reader can weigh a flag.
    submissions: Mapping[str, int]

    @property
    def volunteers(self) -> set[str]:
        """Every volunteer the import would mint."""
        return set(self.by_name.values()) | set(self.by_address.values())

    def addresses(self, key: str) -> frozenset[str]:
        """Every address this volunteer wrote, which may be none."""
        return self.held.get(key, frozenset())

    def volunteer(self, submission: Submission) -> Person:
        """Which volunteer this submission is from."""
        name = spelled(submission.name)
        if name:
            # A spelling this directory has not seen is its own volunteer,
            # which is what the rule says about every spelling nothing joins.
            return Person(self.by_name.get(name, name), How.NAME)
        address = addressed(submission.email)
        if address in self.by_address:
            return Person(self.by_address[address], How.ADDRESS)
        if not address:
            return Person(None, How.NOBODY, "no name and no address")
        return Person(None, How.NOBODY, "the address is written beside more than one volunteer")

    def survivors(self) -> int:
        """The fewest volunteers the workbook can hold under this rule.

        A flag is a question, so the count of rows is the ceiling and this is
        the floor. Deliberately not "if every flag is a duplicate", which is
        what this was once called and is not what it computes: a flag naming
        no candidate has nobody to be a duplicate *of* and survives every
        answer to the others.
        [§6](../../../../docs/briefs/sheet-classifiers.md#6-person-to-volunteer)
        carries the range and how wide it is.
        """
        named = {key: count for key, count in self.submissions.items() if key in self.by_name.values()}
        merges = [(key, other) for key, might_be in self.flagged.items() for other in might_be]
        return len(set(_spoken_for(named, merges).values())) + sum(1 for key in self.flagged if not self.flagged[key])


def directory(sheet: Sheet) -> Directory:
    """Read every submission, and answer who the workbook's people are."""
    written: Counter[str] = Counter()
    typed: dict[str, Counter[str]] = defaultdict(Counter)
    addresses: dict[str, set[str]] = defaultdict(set)
    for one in sheet.submissions:
        name = spelled(one.name)
        if not name:
            continue
        written[name] += 1
        typed[name][one.name] += 1
        if one.email:
            addresses[name].add(addressed(one.email))
    by_name = _spoken_for(
        written,
        [
            (one, other)
            for one, other in combinations(sorted(written), 2)
            if near(one, other) and addresses[one] & addresses[other]
        ],
    )

    held: dict[str, set[str]] = defaultdict(set)
    submissions: Counter[str] = Counter()
    for name, key in by_name.items():
        held[key] |= addresses[name]
        submissions[key] += written[name]

    named_beside: dict[str, set[str]] = defaultdict(set)
    for one in sheet.submissions:
        name = spelled(one.name)
        if name and one.email:
            named_beside[addressed(one.email)].add(by_name[name])
    by_address: dict[str, str] = {}
    # Addresses that became a volunteer because nobody was ever named beside
    # them, recorded here rather than recognised later by the key equalling
    # the address. An address can also *be* somebody's name -- `sean` written
    # in both fields -- and then the volunteer the name resolves to is spelled
    # exactly like the address, so the two are indistinguishable afterwards.
    # Inferring it put a named volunteer under the flag reserved for
    # address-only rows and made the floor come out above the ceiling.
    minted: set[str] = set()
    for one in sheet.submissions:
        address = addressed(one.email)
        if spelled(one.name) or not address or address in by_address:
            continue
        reached = named_beside.get(address, set())
        if len(reached) == 1:
            by_address[address] = next(iter(reached))
        elif not reached:
            # Nobody is ever named beside it, so it is a volunteer of its own
            # and the submission keeps an attribution.
            by_address[address] = address
            minted.add(address)

    volunteers = set(by_name.values())
    flagged: dict[str, tuple[str, ...]] = {}
    for key in sorted(volunteers):
        if held[key]:
            continue
        might_be = tuple(other for other in sorted(volunteers) if other != key and near(key, other))
        if might_be:
            flagged[key] = might_be
    for address in minted:
        flagged[address] = ()
    for one in sheet.submissions:
        if not spelled(one.name) and (address := addressed(one.email)) in by_address:
            submissions[by_address[address]] += 1
    return Directory(
        by_name=by_name,
        spellings={folded: busiest(counted, counted) for folded, counted in typed.items()},
        by_address=by_address,
        flagged=flagged,
        shared={address: frozenset(reaches) for address, reaches in named_beside.items() if len(reaches) > 1},
        held={key: frozenset(written) for key, written in held.items()},
        submissions=submissions,
    )


def section(sheet: Sheet) -> Report:
    """The spellings, the volunteers they come to, and what the rule is unsure of.

    Three counts and not one, because the headcount is a range rather than a
    number: the rows the import mints are the top of it, the flags are what
    stands between that and the bottom, and the bottom is what is left if
    every flag that names a candidate is one. A single figure here would be
    the mistake this section exists to correct.
    """
    spellings = {one.name for one in sheet.submissions if one.name}
    names = {spelling.lower() for spelling in spellings}
    who = directory(sheet)
    volunteers = who.volunteers
    by_name = set(who.by_name.values())
    reached = [who.volunteer(one) for one in sheet.submissions]
    return "People", [
        ("distinct name spellings", len(spellings)),
        ("  the same but for case", len(spellings) - len(names)),
        ("distinct names", len(names)),
        ("  holding no name at all", sum(1 for name in names if not spelled(name))),
        ("  joined to another by a shared address", len(who.by_name) - len(by_name)),
        ("  a volunteer in their own right", len(by_name)),
        ("volunteers the import mints", len(volunteers)),
        ("  known only by an address", len(volunteers) - len(by_name)),
        ("  flagged as possibly a duplicate", len(who.flagged)),
        # Three spaces: this is what the flags on the line above carry, and it
        # counts submissions rather than volunteers, so it is a child of that
        # line and not a share of the one above it.
        ("   submissions those flags carry", sum(1 for person in reached if person.key in who.flagged)),
        ("the fewest volunteers this can be", who.survivors()),
        # Why the headcount is a range and not a number above or below 65.
        # Addresses pull it both ways at once: a volunteer who never wrote one
        # is invisible to an email count, and one who wrote several is counted
        # more than once by it.
        ("volunteers who wrote no address", sum(1 for key in by_name if not who.addresses(key))),
        ("volunteers who wrote more than one", sum(1 for key in by_name if len(who.addresses(key)) > 1)),
        (" the most any one of them wrote", max((len(who.addresses(key)) for key in by_name), default=0)),
        ("addresses written beside more than one volunteer", len(who.shared)),
        (" the most volunteers any one of them names", max((len(named) for named in who.shared.values()), default=0)),
        ("submissions reaching a volunteer", sum(1 for person in reached if person.key)),
        ("  by the name field", sum(1 for person in reached if person.how == How.NAME)),
        ("  by an address, the name being unusable", sum(1 for person in reached if person.how == How.ADDRESS)),
        ("submissions reaching nobody", sum(1 for person in reached if person.how == How.NOBODY)),
        ("not-a-name entries no submission wrote", len(NOT_A_NAME.keys() - names)),
    ]
