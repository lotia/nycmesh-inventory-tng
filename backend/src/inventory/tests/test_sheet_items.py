"""Tests for rule 1, which catalogued item an item string names.

The properties worth pinning are the ones the alias table exists for: case
resolves the way the sheet's own VLOOKUP resolves it, a judgement somebody made
is applied as written, and a string nobody has decided about comes back saying
so rather than quietly resolving to nothing.
"""

import pytest

from inventory.sheet.items import ALIASES, RETIRED_CODE, UNRESOLVABLE, How, Resolution, resolve, section
from inventory.tests.sheets import sheet_of, submission

CATALOGUE = ("LiteBeam", "OmniTikPOE", "SFP-RJ45 Module", "Tp-Link", "mast straight")


def test_an_exact_match_needs_no_judgement() -> None:
    assert resolve("LiteBeam", CATALOGUE) == Resolution("LiteBeam", How.EXACT)


def test_case_resolves_the_way_the_sheets_own_lookup_resolves_it() -> None:
    """VLOOKUP is case-insensitive, so `Mast straight` against the catalogue's
    `mast straight` already resolves today and the rule keeps that.
    """
    resolved = resolve("Mast straight", CATALOGUE)

    assert (resolved.item, resolved.how) == ("mast straight", How.CASE)


def test_the_largest_unmatched_string_resolves_to_the_module_not_the_router() -> None:
    """`TP-Link SFP-RJ45` is 40 submissions, and the catalogue holds both
    `SFP-RJ45 Module` and `Tp-Link`. Read as a typo it mints an identifier
    against the router; it is a TP-Link-branded module.
    """
    assert resolve("TP-Link SFP-RJ45", CATALOGUE).item == "SFP-RJ45 Module"


def test_an_alias_is_matched_however_the_string_is_cased() -> None:
    """`Omnitik` and `omnitik` are both in the ledger and are one decision."""
    assert resolve("Omnitik", CATALOGUE).item == resolve("omnitik", CATALOGUE).item == "OmniTikPOE"


def test_an_ambiguous_string_says_why_rather_than_guessing() -> None:
    """Guessing mints an identifier that sends every later scan of the phrase
    to the wrong shelf.
    """
    resolved = resolve("mast", CATALOGUE)

    assert (resolved.item, resolved.how) == (None, How.UNRESOLVABLE)
    assert resolved.why == "names one of three masts"


def test_a_retired_sku_is_unresolvable_under_one_reason() -> None:
    """46 of the 53 are answered by the same sentence, so they are a pattern
    rather than 46 lines of table saying it again.
    """
    resolved = resolve("NYCM-ER-SXTSQ", CATALOGUE)

    assert (resolved.item, resolved.how) == (None, How.UNRESOLVABLE)
    assert "retired NYCM SKU" in resolved.why


def test_a_retired_sku_that_decodes_to_one_item_resolves_to_it() -> None:
    """The alias table is consulted before the pattern, so the seven that
    decode are not swept up by the reason covering the rest.
    """
    assert resolve("NYCM-ER-LBEG2", CATALOGUE).item == "LiteBeam"


def test_a_string_nobody_has_decided_about_says_so() -> None:
    """This is the outcome the report expects to be zero. A string that
    silently resolved to nothing would look like a decision somebody made.
    """
    assert resolve("Some New Radio", CATALOGUE).how == How.UNACCOUNTED


@pytest.mark.parametrize("string", sorted(UNRESOLVABLE))
def test_no_string_is_both_aliased_and_recorded_unresolvable(string: str) -> None:
    """The two tables are read in order, so an entry in both would be a
    decision that reads one way and behaves the other.
    """
    assert string not in ALIASES


@pytest.mark.parametrize("string", sorted(UNRESOLVABLE))
def test_nothing_recorded_unresolvable_is_a_retired_code(string: str) -> None:
    """A retired code belongs under the one reason that covers them, and an
    entry here would be a second reason for the same string.
    """
    assert not RETIRED_CODE.match(string)


def test_the_report_accounts_for_every_string_and_every_submission() -> None:
    sheet = sheet_of(
        [
            submission(item="LiteBeam"),
            submission(item="litebeam"),
            submission(item="Omnitik"),
            submission(item="mast"),
            submission(item="NYCM-ER-SXTSQ"),
            submission(item=""),
        ],
        catalogue=CATALOGUE,
    )

    _, counted = section(sheet)

    partition = dict(counted)
    assert partition["distinct strings named"] == 5
    assert partition["  matching the catalogue exactly"] == 1
    assert partition["  matching but for case"] == 1
    assert partition["  resolved by a hand-written alias"] == 1
    assert partition["  recorded as naming no catalogued item"] == 2
    assert partition["  neither resolved nor accounted for"] == 0
    assert partition["submissions naming an item"] == 5
    assert partition["  reaching a catalogued item"] == 3
    assert partition["  reaching nothing"] == 2
    assert partition["submissions naming no item at all"] == 1


def test_the_report_notices_an_alias_pointing_at_nothing() -> None:
    """The catalogue is read from the workbook, so an item renamed there would
    otherwise leave an alias resolving to a name that is not in it.
    """
    _, counted = section(sheet_of([submission(item="LiteBeam")], catalogue=("LiteBeam",)))

    assert dict(counted)["alias targets the catalogue does not hold"] == len(set(ALIASES.values()) - {"LiteBeam"})


def test_an_alias_the_catalogue_no_longer_holds_reaches_nothing() -> None:
    """Otherwise the row counts as reaching a catalogued item while the
    self-check reports the same alias as pointing at nothing, and the two
    halves of one report disagree.
    """
    resolved = resolve("omnitik", ("LiteBeam",))

    assert (resolved.item, resolved.how) == (None, How.UNRESOLVABLE)
    assert "the catalogue no longer holds" in resolved.why

    _, counted = section(sheet_of([submission(item="omnitik")], catalogue=("LiteBeam",)))

    partition = dict(counted)
    assert partition["  reaching a catalogued item"] == 0
    assert partition["  reaching nothing"] == 1
    assert partition["  resolved by a hand-written alias"] == 0


def test_normalisation_is_the_one_the_database_uses() -> None:
    """``normalised`` says which column this has to match and who says so."""
    assert resolve("STRASSE", ("Strasse",)).item == "Strasse"
    # casefold() maps this to 'strasse' and lower() does not, so the two
    # readings disagree about whether these are one identifier. The database
    # says they are two.
    assert resolve("STRASSE", ("Straße",)).how == How.UNACCOUNTED
