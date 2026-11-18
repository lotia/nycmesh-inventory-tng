"""Administrators' pages for the catalogue, places, volunteers and devices
(inventory-tng-8i4.14): server-rendered forms, signed in
(admins/guards.py), each change recorded in the history as the
administrator who made it, and logged by id.

Every catalogue page follows one pattern: a list with a form to add, a page
per row to change it, retire it (nothing is deleted) and bring it back. A
page passes its own paths to its templates, so a page shown again after a
refused form still links where it should.
"""

from fastapi import APIRouter

from inventory_tng import pages
from inventory_tng.admin import (
    categories,
    common,
    counts,
    devices,
    items,
    labels,
    length_checks,
    locations,
    over_recorded,
    reading_rules,
    settings,
    stock,
    used_up,
    volunteers,
)
from inventory_tng.admins import home_routes
from inventory_tng.admins import routes as admins_routes

# The sections, in the order the navigation shows them, and their badges.
pages.SECTIONS[:] = (
    (items.ITEMS, common.SECTION_ITEMS),
    (categories.CATEGORIES, common.SECTION_CATEGORIES),
    (locations.LOCATIONS, common.SECTION_LOCATIONS),
    (labels.LABELS, common.SECTION_LABELS),
    (volunteers.VOLUNTEERS, common.SECTION_VOLUNTEERS),
    (stock.STOCK, common.SECTION_STOCK),
    (counts.COUNTS, common.SECTION_COUNTS),
    (devices.DEVICES, common.SECTION_DEVICES),
    (admins_routes.ADMINS, common.SECTION_ADMINS),
    (settings.SETTINGS, common.SECTION_SETTINGS),
)
pages.BADGES[counts.COUNTS] = counts.badge

router = APIRouter(include_in_schema=False)
for module in (
    home_routes,
    items,
    categories,
    locations,
    labels,
    volunteers,
    stock,
    used_up,
    length_checks,
    over_recorded,
    reading_rules,
    counts,
    devices,
    admins_routes,
    settings,
):
    router.include_router(module.router)
