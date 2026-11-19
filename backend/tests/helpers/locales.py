"""Catalogues the tests speak in (core/phrases.speaking) to see how a
language writes numbers: none of their phrases is translated."""

from babel.messages.catalog import Catalog

from inventory_tng.core.phrases import compiled

ENGLISH = compiled(Catalog(locale="en"))
GERMAN = compiled(Catalog(locale="de"))
