"""Fixtures shared by the catalogue and ledger tests.

They describe one small, real scene -- a warehouse, a volunteer holding stock,
and a radio -- so tests in either module read as statements about NYC Mesh
rather than about test data.
"""

import pytest
from django.core.cache import cache

from inventory.models import Category, Item, Location, Volunteer


@pytest.fixture(autouse=True)
def _forget_throttle_history() -> None:
    """Rate-limit counters live in the cache, which no transaction rolls back.

    Without this, one test's writes are counted against the next one's limit
    and whether a test sees a 429 depends on what ran before it.
    """
    cache.clear()


@pytest.fixture
def category() -> Category:
    return Category.objects.create(name="Radios")


@pytest.fixture
def item(category: Category) -> Item:
    return Item.objects.create(name="LiteBeam", category=category)


@pytest.fixture
def volunteer() -> Volunteer:
    return Volunteer.objects.create(display_name="Sean")


@pytest.fixture
def warehouse() -> Location:
    return Location.objects.create(name="131 Broome", kind=Location.Kind.WAREHOUSE)


@pytest.fixture
def custody(volunteer: Volunteer) -> Location:
    return Location.objects.create(
        name="Sean",
        kind=Location.Kind.VOLUNTEER_CUSTODY,
        held_by=volunteer,
    )
