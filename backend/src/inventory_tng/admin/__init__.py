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
from inventory_tng.admin import common, settings
from inventory_tng.admins import home_routes
from inventory_tng.admins import routes as admins_routes
from inventory_tng.catalogue import (
    categories_routes,
    items_routes,
    reading_rules_routes,
)
from inventory_tng.devices import routes as devices_routes
from inventory_tng.labels import routes as labels_routes
from inventory_tng.places import routes as places_routes
from inventory_tng.stock import (
    counts_routes,
    length_checks_routes,
    over_recorded_routes,
    stock_routes,
    used_up_routes,
)
from inventory_tng.volunteers import routes as volunteers_routes

# The sections, in the order the navigation shows them, and their badges.
pages.SECTIONS[:] = (
    (items_routes.ITEMS, common.SECTION_ITEMS),
    (categories_routes.CATEGORIES, common.SECTION_CATEGORIES),
    (places_routes.LOCATIONS, common.SECTION_LOCATIONS),
    (labels_routes.LABELS, common.SECTION_LABELS),
    (volunteers_routes.VOLUNTEERS, common.SECTION_VOLUNTEERS),
    (stock_routes.STOCK, common.SECTION_STOCK),
    (counts_routes.COUNTS, common.SECTION_COUNTS),
    (devices_routes.DEVICES, common.SECTION_DEVICES),
    (admins_routes.ADMINS, common.SECTION_ADMINS),
    (settings.SETTINGS, common.SECTION_SETTINGS),
)
pages.BADGES[counts_routes.COUNTS] = counts_routes.badge

router = APIRouter(include_in_schema=False)
for module in (
    home_routes,
    items_routes,
    categories_routes,
    places_routes,
    labels_routes,
    volunteers_routes,
    stock_routes,
    used_up_routes,
    length_checks_routes,
    over_recorded_routes,
    reading_rules_routes,
    counts_routes,
    devices_routes,
    admins_routes,
    settings,
):
    router.include_router(module.router)
