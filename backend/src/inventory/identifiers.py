"""The one form two spellings of an identifier have to agree on.

The rule, the measurements behind it and the three mechanisms that were weighed
are [decision 0026](../../../docs/decisions/0026-what-makes-two-strings-one-identifier.md).
What is here is that decision expressed as something PostgreSQL computes:
`ItemIdentifier.value_normalised` is generated from `Canonical("value")` and
carries the unique index, so no write path can hold a different opinion about
whether two strings are one identifier.
"""

import re
import unicodedata
from typing import Any

from django.db.models import Func, Q, Value

# Every character Unicode gives the White_Space property. `TRIM` strips U+0020
# and nothing else, which is the whole of the defect this closes: a value
# padded with a tab or a no-break space renders identically to one that is not,
# and takes a second row under an index that exists to refuse it.
#
# Built from codepoints rather than written as characters, because most of them
# are invisible in an editor. A literal here would be a character nobody could
# see while reviewing this file, in the one file where that matters most.
SPACES = "".join(
    map(
        chr,
        (
            0x09,  # character tabulation
            0x0A,  # line feed
            0x0B,  # line tabulation
            0x0C,  # form feed
            0x0D,  # carriage return
            0x20,  # space, the only one TRIM knows
            0x85,  # next line
            0xA0,  # no-break space, which is what a word processor gives you
            0x1680,  # ogham space mark
            *range(0x2000, 0x200B),  # en quad through hair space
            0x2028,  # line separator
            0x2029,  # paragraph separator
            0x202F,  # narrow no-break space
            0x205F,  # medium mathematical space
            0x3000,  # ideographic space
        ),
    )
)

# Not whitespace under any definition -- these have no width at all -- but
# invisible in exactly the way that defeats a unique index. They are removed
# rather than collapsed, because one of them between two letters is not a word
# break: folding it to a space would make `Lite<zwsp>Beam` and `Lite Beam` one
# string, which is a different claim and not one anything here has measured.
INVISIBLES = "".join(map(chr, (0xAD, 0x200B, 0xFEFF)))  # soft hyphen, ZWSP, BOM

# One spelling of the pattern for both engines. PostgreSQL and Python agree
# about a bracket expression over literal characters, which is the only regex
# feature either side uses here.
RUN_OF_SPACES = f"[{SPACES}]+"

_RUNS = re.compile(RUN_OF_SPACES)
_DROPPED = {ord(character): None for character in INVISIBLES}


class Canonical(Func):
    """``value`` reduced to the form its duplicates share.

    NFC first, so a composed accent and a decomposed one are one string; then
    the invisibles go and every run of whitespace becomes a single ASCII space,
    trimmed at both ends; then lower case.

    Every function in that chain is ``IMMUTABLE``, which is what makes it legal
    in a generated column -- and which Django does not check. An expression
    that is merely ``STABLE`` passes ``makemigrations``, ships in a committed
    migration and fails when somebody runs ``migrate``, which is why
    ``unaccent()`` is not here and cannot be.

    The collapse and the trim are written in that order only because one had to
    go first; over this character set either order gives the same answer.
    """

    # Nested calls rather than one hand-written template, so that the two
    # character sets arrive as bound parameters instead of being spliced into
    # DDL as a literal run of invisible characters.
    def __init__(self, expression: Any, **extra: Any) -> None:
        composed = Func(expression, function="normalize", template="%(function)s(%(expressions)s, NFC)")
        visible = Func(composed, Value(INVISIBLES), Value(""), function="translate")
        spaced = Func(visible, Value(RUN_OF_SPACES), Value(" "), Value("g"), function="regexp_replace")
        super().__init__(Func(spaced, Value(" "), function="btrim"), function="lower", **extra)


def normalised(string: str) -> str:
    """``Canonical`` as Python computes it, which is not the same thing.

    **This is not the authority on whether two strings are one identifier, and
    nothing may use it as one.** PostgreSQL's ``lower()`` and Python's
    ``str.lower()`` disagree, in both directions, and the disagreement cannot
    be closed: one of them varies with the database's collation and the other
    does not. ``casefold()`` is a third answer again. Decision 0026 names the
    characters, and measured them rather than recalling them.

    So the unique index on ``value_normalised`` is what decides, an
    ``IntegrityError`` raised by it is an ordinary answer to be reported rather
    than a fault to be asserted away, and this function exists only so that the
    overwhelming majority of strings -- the ones both folds agree about -- can
    be matched without asking the database twice.

    Whatever reads this and then writes a row has to handle the database
    disagreeing with it. ``_identifiers.mint`` is the worked example.
    """
    visible = unicodedata.normalize("NFC", string).translate(_DROPPED)
    return _RUNS.sub(" ", visible).strip(" ").lower()


def matching(typed: str) -> Q:
    """Identifiers that begin with what somebody has typed.

    The one place that knows how this column is searched, because getting it
    wrong is invisible: the query returns the right rows either way and only
    the plan is different.

    ``__startswith`` against a term lowered here, never ``__istartswith``.
    Django compiles the latter to ``UPPER(col) LIKE UPPER(pattern)``, and an
    index is over a column rather than over a function of one, so that form
    matches nothing and reads the whole table. The index this pairs with is
    ``item_identifier_prefix``.

    Folding in Python is safe *here* in a way it is not on a write path: the
    worst a disagreement can do to a search is offer a row somebody did not
    mean or miss one they did, and the next keystroke corrects it. Nothing is
    stored, so nothing can be stored wrongly.
    """
    return Q(value_normalised__startswith=normalised(typed))
