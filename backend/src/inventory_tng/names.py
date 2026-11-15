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

# Each character that looks like an ASCII space, quote or dash, and the one
# it is read as before IGNORED. Listed one by one, not by Unicode category,
# so PostgreSQL's translate() reads the same list whatever its version.
LOOK_ALIKES = {
    # Spaces: Unicode's (Zs), but the space itself.
    "\u00a0": " ",  # no-break space
    "\u1680": " ",  # Ogham space mark
    "\u2000": " ",  # en quad
    "\u2001": " ",  # em quad
    "\u2002": " ",  # en space
    "\u2003": " ",  # em space
    "\u2004": " ",  # three-per-em space
    "\u2005": " ",  # four-per-em space
    "\u2006": " ",  # six-per-em space
    "\u2007": " ",  # figure space
    "\u2008": " ",  # punctuation space
    "\u2009": " ",  # thin space
    "\u200a": " ",  # hair space
    "\u202f": " ",  # narrow no-break space
    "\u205f": " ",  # medium mathematical space
    "\u3000": " ",  # ideographic space
    # Apostrophes and single quotes.
    "\u00b4": "'",  # acute accent
    "\u02bc": "'",  # modifier letter apostrophe
    "\u2018": "'",  # left single quotation mark
    "\u2019": "'",  # right single quotation mark
    "\u201a": "'",  # single low-9 quotation mark
    "\u201b": "'",  # single high-reversed-9 quotation mark
    "\u2032": "'",  # prime
    "\u2035": "'",  # reversed prime
    "\uff07": "'",  # fullwidth apostrophe
    # Double quotes.
    "\u201c": '"',  # left double quotation mark
    "\u201d": '"',  # right double quotation mark
    "\u201e": '"',  # double low-9 quotation mark
    "\u201f": '"',  # double high-reversed-9 quotation mark
    "\u2033": '"',  # double prime
    "\u2036": '"',  # reversed double prime
    "\uff02": '"',  # fullwidth quotation mark
    # Dashes and hyphens.
    "\u00ad": "-",  # soft hyphen
    "\u2010": "-",  # hyphen
    "\u2011": "-",  # non-breaking hyphen
    "\u2012": "-",  # figure dash
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2015": "-",  # horizontal bar
    "\u2212": "-",  # minus sign
    "\ufe58": "-",  # small em dash
    "\ufe63": "-",  # small hyphen-minus
    "\uff0d": "-",  # fullwidth hyphen-minus
}
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
