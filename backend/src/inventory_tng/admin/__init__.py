"""Administrators' pages for the catalogue, places, volunteers and devices
(inventory-tng-8i4.14): server-rendered forms, signed in (sign_in.py), each
change recorded in the history as the administrator who made it.

Every catalogue page follows one pattern: a list with a form to add, a page
per row to rename it, retire it (nothing is deleted) and bring it back.
"""

from fastapi import APIRouter

from inventory_tng.admin import categories, home

router = APIRouter(include_in_schema=False)
router.include_router(home.router)
router.include_router(categories.router)
