"""Where stock is: locations."""

from sqlalchemy import CheckConstraint, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column, validates

from inventory_tng.core.db import Base, enforce, protection
from inventory_tng.core.time_zones import is_time_zone
from inventory_tng.models.base import (
    CreatedAt,
    IntId,
    RetiredAt,
    Versioned,
    one_of,
    reference,
    unique_name,
)
from inventory_tng.models.names import CanonicalName
from inventory_tng.models.search_text import Searchable

LOCATION_KINDS = ("store", "area", "vehicle", "custody")
# The stores and the areas in them (an area sits only in a store or another
# area): where stock counts towards reordering (docs/decisions/0003).
STORE_KINDS = ("store", "area")


class Location(CanonicalName, Searchable, Versioned, Base):
    """Where stock is (docs/decisions/0003). Areas nest under a store or
    another area; a volunteer holding stock is a custody location."""

    __tablename__ = "location"
    __table_args__ = (
        # Among siblings of a kind, not the whole tree: two stores may each
        # have "Shelf A", and a van may share a volunteer's name. Retired
        # ones too, but a volunteer's custody, retired and made again under
        # their name, only among current ones.
        unique_name(
            "location", "kind", "coalesce(parent_id, 0)", where="kind <> 'custody'"
        ),
        unique_name(
            "location",
            where="kind = 'custody' AND retired_at IS NULL",
            name="custody_name",
        ),
        Index(
            "uq_location_custody_volunteer_id",
            "volunteer_id",
            unique=True,
            postgresql_where=text("kind = 'custody' AND retired_at IS NULL"),
        ),
        CheckConstraint(one_of("kind", LOCATION_KINDS), name="kind"),
        CheckConstraint(
            "(kind = 'area') = (parent_id IS NOT NULL)", name="area_has_parent"
        ),
        CheckConstraint(
            "(kind = 'custody') = (volunteer_id IS NOT NULL)",
            name="custody_has_volunteer",
        ),
        protection(owner="places/locations.py", lock="catalogue", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    parent_id: Mapped[int | None] = reference("location.id")
    volunteer_id: Mapped[int | None] = reference("volunteer.id")
    retired_at: Mapped[RetiredAt]
    # Its IANA zone, if not its parent's, or the site's at the top
    # (local_time.zone_at; docs/decisions/0013).
    time_zone: Mapped[str | None] = mapped_column(Text)

    @validates("time_zone")
    def _known_zone(self, _key: str, value: str | None) -> str | None:
        """Refuses, as it is set, a zone tzdata does not know (RuleBroken
        "location_time_zone")."""
        enforce(
            "location_time_zone", when=value is not None and not is_time_zone(value)
        )
        return value
