"""What attempt() says a refused change broke."""

from sqlalchemy.orm import Session

from inventory_tng.core.db_errors import Unique, attempt, refusal, unique_index
from inventory_tng.models.catalogue import Category, Item

from helpers.rows import add


def test_a_unique_refusal_names_its_index(session: Session, radios: Category) -> None:
    """Still "unique" to every page, it names the index the database
    said was broken."""
    add(session, Item(name="LiteBeam", category_id=radios.id, old_code="NYCM-1"))

    def clash() -> Item:
        return add(session, Item(name="Omni", category_id=radios.id, old_code="NYCM-1"))

    done, broken = attempt(session, clash)
    assert done is None
    assert broken == "unique"
    assert unique_index(broken) == "uq_item_old_code"
    assert refusal(session, clash, "unique")[1] == "unique"


def test_only_a_unique_refusal_names_an_index() -> None:
    assert unique_index("item_name_is_alias") is None
    assert unique_index(None) is None
    assert unique_index(Unique(None)) is None
    assert Unique("uq_item_name") == "unique"
