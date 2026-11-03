"""Administrators' pages for the catalogue, places, volunteers and devices
(inventory-tng-8i4.14): server-rendered forms, signed in (sign_in.py), each
change recorded in the history as the administrator who made it, and logged
by id.

Every catalogue page follows one pattern: a list with a form to add, a page
per row to change it, retire it (nothing is deleted) and bring it back. A
page passes its own paths to its templates, so a page shown again after a
refused form still links where it should.
"""

from typing import Any, cast

from fastapi import APIRouter

from inventory_tng.admin import (
    admins,
    categories,
    counts,
    devices,
    home,
    items,
    locations,
    settings,
    stock,
    volunteers,
)
from inventory_tng.pages import templates

# The sections, in the order the navigation shows them.
SECTIONS = (
    (items.ITEMS, "Items"),
    (categories.CATEGORIES, "Categories"),
    (locations.LOCATIONS, "Locations"),
    (volunteers.VOLUNTEERS, "Volunteers"),
    (stock.STOCK, "Stock"),
    (counts.COUNTS, "Counts"),
    (devices.DEVICES, "Devices"),
    (admins.ADMINS, "Administrators"),
    (settings.SETTINGS, "Settings"),
)
cast(dict[str, Any], templates.env.globals)["sections"] = SECTIONS

router = APIRouter(include_in_schema=False)
for module in (
    home,
    items,
    categories,
    locations,
    volunteers,
    stock,
    counts,
    devices,
    admins,
    settings,
):
    router.include_router(module.router)
