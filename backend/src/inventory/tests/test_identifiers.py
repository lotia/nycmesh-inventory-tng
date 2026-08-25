"""What the database treats as one identifier, asked of the database.

Every test that matters here goes through PostgreSQL rather than through
`inventory.identifiers.normalised`. That is the point of decision 0026: the
generated column is the authority and the Python fold is a convenience, so a
test that only exercised the convenience would pass while the thing it stands
for was broken.
"""

import unicodedata
from importlib import import_module

import pytest
from django.apps import apps
from django.db import IntegrityError, connection, transaction

from inventory.identifiers import INVISIBLES, SPACES, matching, normalised
from inventory.models import Item, ItemIdentifier

pytestmark = pytest.mark.django_db


def alias(item: Item, value: str) -> ItemIdentifier:
    return ItemIdentifier.objects.create(item=item, kind=ItemIdentifier.Kind.ALIAS, value=value)


def refuses(item: Item, value: str) -> bool:
    """Whether the unique index turns ``value`` away."""
    try:
        with transaction.atomic():
            alias(item, value)
    except IntegrityError:
        return True
    return False


# --------------------------------------------------------------------------
# The three duplicates the old expression accepted, each demonstrated as one.
# --------------------------------------------------------------------------


def test_a_decomposed_accent_is_the_same_identifier(item: Item) -> None:
    """`Café` spelled two ways is one string, and only NFC says so.

    The two differ in byte length and in nothing a person can see, so without
    this the catalogue can hold both and a scan of either resolves to whichever
    was written first.
    """
    composed = unicodedata.normalize("NFC", "Café")
    decomposed = unicodedata.normalize("NFD", "Café")
    assert composed != decomposed, "the fixture is wrong: these are meant to differ as bytes"

    alias(item, composed)

    assert refuses(item, decomposed)


@pytest.mark.parametrize(
    ("padded", "why"),
    [
        ("\tLiteBeam", "a leading tab"),
        ("LiteBeam\r\n", "a trailing carriage return and line feed"),
        ("\xa0LiteBeam\xa0", "no-break spaces, which is what a word processor gives you"),
        ("\u2003LiteBeam", "an em space"),
        ("\u3000LiteBeam", "an ideographic space"),
    ],
)
def test_whitespace_trim_does_not_strip_is_still_stripped(item: Item, padded: str, why: str) -> None:
    """Every one of these defeated the old expression."""
    alias(item, "LiteBeam")

    assert refuses(item, padded), why


def test_a_doubled_internal_space_is_the_same_identifier(item: Item) -> None:
    alias(item, "Mast straight")

    assert refuses(item, "Mast  straight")


@pytest.mark.parametrize(
    ("hidden", "why"),
    [
        ("Lite\u00adBeam", "a soft hyphen"),
        ("Lite\u200bBeam", "a zero-width space"),
        ("\ufeffLiteBeam", "a byte-order mark"),
    ],
)
def test_a_character_with_no_width_does_not_make_a_second_identifier(item: Item, hidden: str, why: str) -> None:
    """A character with no width does not make a second identifier."""
    alias(item, "LiteBeam")

    assert refuses(item, hidden), why


def test_an_invisible_between_words_does_not_join_them(item: Item) -> None:
    """The other half of the rule above, and the reason the two sets differ."""
    alias(item, "Lite Beam")

    assert not refuses(item, "Lite\u200bBeam"), "a zero-width space was folded to a space, which it is not"


# --------------------------------------------------------------------------
# What the fold must NOT do. Decision 3: case and whitespace, nothing else.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("one", "other"),
    [
        ("RJ45 passthrough", "RJ-45 passthrough"),
        ("Netpower 15R", "Netpower 16P"),
        ("UAP-AC-M", "UAP-AC-IW"),
        ("CubeSA", "Cube"),
        ("SFP-RJ45 Module", "SFPRJ45 Module"),
    ],
)
def test_punctuation_and_digits_still_tell_products_apart(item: Item, one: str, other: str) -> None:
    """Folding harder would merge real products; decision 3 has the counts."""
    alias(item, one)

    assert not refuses(item, other), f"{one!r} and {other!r} became one identifier"


def test_case_is_folded_because_that_is_the_whole_request(item: Item) -> None:
    alias(item, "LiteBeam")

    assert refuses(item, "litebeam")


# --------------------------------------------------------------------------
# Decision 4. The Python fold is a convenience and the database is the rule,
# so the two are asserted to agree only where they actually do.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "string",
    [
        "LiteBeam",
        "  Mast   straight  ",
        "\tTP-Link\xa0SFP-RJ45\r\n",
        "Caf\u00e9",  # composed
        "Cafe\u0301",  # decomposed, and identical on screen
        "Lite\u200bBeam",
        "OmniTIK PoE",
        "NYCM-ER-LBEG2",
    ],
)
def test_the_python_fold_agrees_with_the_database_on_ordinary_strings(item: Item, string: str) -> None:
    """The common case, which is what `normalised` exists to make fast.

    Asked of PostgreSQL rather than asserted from a table written here, because
    a table written here would be a second implementation of the rule and the
    drift it would hide is exactly the one decision 4 is about.
    """
    stored = alias(item, string)

    assert stored.value_normalised == normalised(string)


def test_the_python_fold_is_not_the_authority_and_the_database_wins(item: Item) -> None:
    """The disagreement decision 4 rests on, asked of a real server.

    Both halves are asserted, because either one passing alone would be the
    interesting result: if this ever fails, the two folds have converged and
    decision 4 is worth re-reading. It is not a licence to let Python decide.
    """
    turkish = "İSTANBUL"

    assert normalised(turkish) != normalised("ISTANBUL"), "Python no longer disagrees; see decision 0026"

    alias(item, "ISTANBUL")

    assert refuses(item, turkish), "PostgreSQL no longer folds U+0130 to i; see decision 0026"


def test_the_stored_value_keeps_the_spelling_that_was_typed(item: Item) -> None:
    """Normalising is for comparison. `value` is evidence and is untouched."""
    typed = "  TP-Link\xa0SFP-RJ45 "

    stored = alias(item, typed)
    stored.refresh_from_db()

    assert stored.value == typed
    assert stored.value_normalised == "tp-link sfp-rj45"


# --------------------------------------------------------------------------
# Decision 6. The index, and the lookup that can reach it.
# --------------------------------------------------------------------------


def test_the_prefix_search_does_not_wrap_the_column_in_a_function() -> None:
    """The guard that stops `__istartswith` coming back.

    Both spellings return the same rows, so only the compiled SQL can tell
    them apart -- which is why `identifiers.matching` exists and why this
    asserts over SQL rather than over results.
    """
    sql = str(ItemIdentifier.objects.filter(matching("LiteB")).query)

    assert "LIKE" in sql.upper(), sql
    assert "UPPER(" not in sql.upper(), sql


def test_istartswith_is_still_the_thing_being_guarded_against() -> None:
    """Pins the reason, not just the rule.

    If Django ever stops compiling `__istartswith` to `UPPER(col) LIKE
    UPPER(...)`, the guard above is defending against something that no longer
    exists and decision 6 is worth re-reading. This is what says so.
    """
    sql = str(ItemIdentifier.objects.filter(value_normalised__istartswith="LiteB").query).upper()

    assert "UPPER(" in sql, "the case decision 0026 measured has changed; re-read decision 6"


def test_the_index_exists_over_the_column_with_byte_ordering() -> None:
    """`text_pattern_ops`, which is what makes a prefix a range.

    Read off the database rather than off `Meta.indexes`, because the model
    saying so and the table having it are different claims and a migration is
    what stands between them.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename = %s AND indexname = %s",
            ["inventory_itemidentifier", "item_identifier_prefix"],
        )
        found = cursor.fetchone()

    assert found is not None, "the prefix index is not on the table"
    assert "value_normalised text_pattern_ops" in found[0]


def test_the_unique_index_alone_cannot_serve_a_prefix_query(item: Item) -> None:
    """Why a second index over the same column is not redundant.

    With sequential scans off the planner must reach for an index, and the one
    it can use is this one. Had the unique index been able to serve a prefix
    query, the model would not need to declare another.
    """
    for spelling in ("LiteBeam", "Litebeam 5AC", "Omni DC", "Mast straight"):
        alias(item, spelling)

    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan TO off")
        plan = ItemIdentifier.objects.filter(matching("liteb")).explain()

    assert "item_identifier_prefix" in plan, plan
    assert "item_identifier_unique_normalised_value" not in plan


# The two character sets, pinned as codepoints.
#
# Pinned because nothing else pins them. Django compares generated fields by
# their deconstructed form, which is `Canonical('value')` whatever `Canonical`
# computes, so changing this class produces NO migration -- `makemigrations
# --check` stays quiet. Databases built before the change keep the old column
# and databases built after it get the new one, and because the column carries
# a unique index those two populations disagree about which rows may exist.
#
# Codepoints rather than the SQL `pg_get_expr` hands back: that rendering is
# the deparser's choice of spacing and casts, it has changed between major
# versions before, and a failure made of invisible characters after a server
# upgrade is one people learn to paper over. These lists are what actually
# changes when somebody edits the rule.
FOLDED_AS_SPACE = [
    0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0x85, 0xA0, 0x1680,
    0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005,
    0x2006, 0x2007, 0x2008, 0x2009, 0x200A,
    0x2028, 0x2029, 0x202F, 0x205F, 0x3000,
]  # fmt: skip
REMOVED_ENTIRELY = [0x00AD, 0x200B, 0xFEFF]


def test_the_folded_characters_are_the_ones_last_agreed_to() -> None:
    """Changing the rule has to be a deliberate act, with a migration.

    If this fails and the change was meant, the fix is not to edit these lists
    on their own: it is to write the next migration too, because every database
    created before the change still has the old column and nothing else will
    say so.
    """
    assert sorted(map(ord, SPACES)) == FOLDED_AS_SPACE
    assert sorted(map(ord, INVISIBLES)) == REMOVED_ENTIRELY


def test_the_column_applies_those_characters_in_the_agreed_order() -> None:
    """The chain, asserted structurally so a deparser change cannot break it.

    NFC before anything else, because the invisibles and the spaces are matched
    by codepoint and a decomposed string has different ones; the trim after the
    collapse, so that whitespace turned into a space can still be trimmed; and
    the case fold last, over whatever the rest produced.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT pg_get_expr(d.adbin, d.adrelid)
            FROM pg_attrdef d
            JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
            WHERE d.adrelid = 'inventory_itemidentifier'::regclass
              AND a.attname = 'value_normalised'
            """
        )
        stored = cursor.fetchone()[0].lower()

    applied = [step for step in ("normalize", "translate", "regexp_replace", "btrim", "lower") if step in stored]
    assert applied == ["normalize", "translate", "regexp_replace", "btrim", "lower"]
    # Innermost first: the string reads outside-in, so the order of application
    # is the reverse of the order the names appear in.
    assert stored.index("lower") < stored.index("btrim") < stored.index("regexp_replace")
    assert stored.index("regexp_replace") < stored.index("translate") < stored.index("normalize")


def test_a_value_whose_normal_form_is_longer_than_itself_is_accepted(item: Item) -> None:
    """NFC lengthens some strings, and the two columns are sized for that.

    U+0958 is one of Unicode's composition exclusions: it is one character and
    its NFC form is two. 150 of them sit well inside `value`, and used to
    overflow `value_normalised` -- as a `DataError`, which is not an
    `IntegrityError` and so is not something the importer can report as a
    conflict. It aborted the run instead.
    """
    stretching = "\u0958" * 150  # one character; two after NFC

    stored = alias(item, stretching)
    stored.refresh_from_db()

    assert len(stored.value) == 150
    assert len(stored.value_normalised) == 300


# --------------------------------------------------------------------------
# The migration's report. It runs before anything is changed, so what it says
# is the only thing an administrator has to act on.
# --------------------------------------------------------------------------


def test_the_collision_report_can_be_read(item: Item) -> None:
    """Two rows that look identical must not be reported identically.

    The pairs this report exists for are the ones nobody can tell apart by
    eye, and a composed accent beside a decomposed one is the worst of them:
    `repr` renders both as `'Café Kit'`, so the report would ask somebody to
    choose between two lines that look the same. Asserting the whole message
    is ASCII pins that, because it is the property `repr` lacks -- it escapes
    a no-break space but not a combining mark.

    The unique constraint is dropped first, inside the test's own transaction,
    because the state this report describes is by definition one the finished
    migration forbids. It comes back when the transaction rolls back.
    """
    migration = import_module("inventory.migrations.0018_the_whitespace_trim_does_not_strip")

    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE inventory_itemidentifier DROP CONSTRAINT item_identifier_unique_normalised_value")
    composed = alias(item, unicodedata.normalize("NFC", "Café Kit"))
    decomposed = alias(item, unicodedata.normalize("NFD", "Café Kit"))
    assert composed.value != decomposed.value, "the fixture is wrong: these are meant to differ as bytes"

    with pytest.raises(RuntimeError) as refusal:
        migration.report_collisions(apps, None)

    report = str(refusal.value)
    assert report.isascii(), report
    assert f"id={composed.pk}" in report
    assert f"id={decomposed.pk}" in report


def test_the_collision_report_says_nothing_when_there_is_nothing_to_say(item: Item) -> None:
    """The ordinary case: it returns, and the migration carries on."""
    migration = import_module("inventory.migrations.0018_the_whitespace_trim_does_not_strip")
    alias(item, "LiteBeam")

    assert migration.report_collisions(apps, None) is None
