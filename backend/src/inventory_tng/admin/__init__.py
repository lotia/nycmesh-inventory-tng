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
    common,
    counts,
    devices,
    labels,
    length_checks,
    over_recorded,
    settings,
    stock,
    used_up,
    volunteers,
)
from inventory_tng.admins import home_routes
from inventory_tng.admins import routes as admins_routes
from inventory_tng.catalogue import (
    categories_routes,
    items_routes,
    reading_rules_routes,
)
from inventory_tng.places import routes as places_routes

# The sections, in the order the navigation shows them, and their badges.
pages.SECTIONS[:] = (
    (items_routes.ITEMS, common.SECTION_ITEMS),
    (categories_routes.CATEGORIES, common.SECTION_CATEGORIES),
    (places_routes.LOCATIONS, common.SECTION_LOCATIONS),
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
    items_routes,
    categories_routes,
    places_routes,
    labels,
    volunteers,
    stock,
    used_up,
    length_checks,
    over_recorded,
    reading_rules_routes,
    counts,
    devices,
    admins_routes,
    settings,
):
    router.include_router(module.router)
