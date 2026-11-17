"""What makes an item's or a package's name its own: its key, the name as
two names are compared (docs/decisions/0021). Lower case, without spaces
and punctuation, so "ToughCable" and "Tough cable" clash; but a decimal
point between two digits is kept, so "305 m" and "30.5 m" do not; a comma
is always punctuation, so "1,000 ft" and "1000 ft" clash (US number
conventions: 0021, 2026-11-12).

Each such row keeps its key in `name_key`, written by the app as the name is
set (models.KeyedName), and unique in an index of its own. A CHECK
constraint holds it to the name in PostgreSQL (name_key_sql), a declaration
within docs/decisions/0016, so a key written wrong, by SQL or a bug, is
refused. So the rule is said twice, in Python and in SQL, from one pattern
and one table, and a test checks they agree on every character. Changing
the rule changes every key: a migration writes the CHECKs again and
refills the keys, naming by id any rows that would then clash.

Only ASCII's spaces and punctuation are ignored, and only ASCII's letters
lowered, whatever the database's locale: as a C locale has it, which
CloudNativePG makes by default, so letters such as `â` and `Â` are kept
apart, and so are signs beyond ASCII. But a space, quote or dash that
looks like ASCII's is read as ASCII's first (LOOK_ALIKES), so a name
pasted with a non-breaking space, a curly quote or an en dash clashes with
the name typed plainly (0021, 2026-11-15); the name itself keeps them. A
name needs a letter or a digit too (nameless), Unicode's, which no key
drops.
"""

import re
import string
import unicodedata

from sqlalchemy import ColumnElement, SQLColumnExpression, collate, func

# What a key leaves out, in a pattern PostgreSQL and Python read alike: a
# space (tab to carriage return, and the space) or a punctuation mark of
# ASCII's (C's isspace and ispunct), but not a point between two digits.
# Ranges of code points, not [[:space:][:punct:]], which Python lacks.
IGNORED = r"(?!(?<=[0-9])[.][0-9])[\t-\r -/:-@[-`{-~]"

# The characters that look like an ASCII space, quote or dash, by the one
# each is read as before IGNORED. Listed by code point, not by Unicode
# category, so PostgreSQL's translate() reads the same list whatever its
# version.
_LOOK_ALIKE = {
    # Unicode's spaces (Zs) but the space itself: no-break, Ogham, en quad
    # to hair, narrow no-break, medium mathematical, ideographic.
    " ": (0x00A0, 0x1680, *range(0x2000, 0x200B), 0x202F, 0x205F, 0x3000),
    # Acute accent, modifier apostrophe, single quotation marks (left,
    # right, low-9, high-reversed-9), prime, reversed prime, fullwidth.
    "'": (0x00B4, 0x02BC, *range(0x2018, 0x201C), 0x2032, 0x2035, 0xFF07),
    # Double quotation marks (left, right, low-9, high-reversed-9), double
    # prime, reversed double prime, fullwidth.
    '"': (*range(0x201C, 0x2020), 0x2033, 0x2036, 0xFF02),
    # Soft hyphen, hyphen to horizontal bar, minus sign, small em dash,
    # small hyphen-minus, fullwidth hyphen-minus.
    "-": (0x00AD, *range(0x2010, 0x2016), 0x2212, 0xFE58, 0xFE63, 0xFF0D),
}
LOOK_ALIKES = {
    chr(code): plain for plain, codes in _LOOK_ALIKE.items() for code in codes
}
# Every space (Zs) but the space itself, each read as the space: units
# and items read any of them as one (inventory-tng-vw0f).
SPACES = "".join(map(chr, _LOOK_ALIKE[" "]))
# Between a number and its unit in a name as shown, and between thousands
# in a number of five digits or more, as the SI Brochure writes them (Ali,
# 2026-11-15, inventory-tng-ist.3): neither breaks a line.
NO_BREAK_SPACE = "\u00a0"
THIN_NO_BREAK_SPACE = "\u202f"
# Each breaking space as the shown name makes it inside a number grouped
# by spaces, so the number never breaks across a line (Ali, 2026-11-16,
# inventory-tng-mz31): thin, hair, punctuation, six-per-em and medium
# mathematical spaces narrow no-break, the others no-break. The no-break
# ones (no-break, figure, narrow no-break) are kept.
_NARROW = "\u2009\u200a\u2008\u2006\u205f"
NO_BREAK = str.maketrans(
    {
        space: THIN_NO_BREAK_SPACE if space in _NARROW else NO_BREAK_SPACE
        for space in " " + SPACES
        if space not in (NO_BREAK_SPACE, "\u2007", THIN_NO_BREAK_SPACE)
    }
)
_FROM = "".join(LOOK_ALIKES)
_TO = "".join(LOOK_ALIKES.values())

_IGNORED = re.compile(IGNORED)
_PLAIN = str.maketrans(LOOK_ALIKES)
_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def name_key(name: str) -> str:
    """`name`'s key, as the app writes it."""
    return _IGNORED.sub("", name.translate(_PLAIN)).translate(_LOWER)


def name_key_sql(name: SQLColumnExpression[str]) -> ColumnElement[str]:
    """`name`'s key in SQL, as the CHECK constraints hold it: collated "C",
    so lower() lowers ASCII's letters only, as name_key does, whatever the
    database's locale."""
    plain = func.translate(collate(name, "C"), _FROM, _TO)
    return func.lower(func.regexp_replace(plain, IGNORED, "", "g"))


def nameless(name: str) -> bool:
    """Whether `name` has no letter or digit, Unicode's: spaces,
    punctuation and signs alone, which tell no name from another (a sign
    such as `±` is kept in its key, but is no name)."""
    return not any(unicodedata.category(c)[0] in "LN" for c in name)
