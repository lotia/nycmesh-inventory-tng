"""How a place's count form is read (stock/count_forms.py)."""

import pytest

from inventory_tng.models.base import INTEGER_MAX
from inventory_tng.stock.count_forms import lines, named_twice


@pytest.mark.parametrize(
    ("form", "twice"),
    [
        ({"counted_5": "1"}, set()),
        ({"counted_5": "1", "counted_6": "2"}, set()),
        ({"counted_5": "1", "counted_05": "2"}, {"counted_5", "counted_05"}),
        ({"counted_5": "1", "counted_005": ""}, {"counted_5", "counted_005"}),
        (
            {"counted_5": "1", "extra_item_id": "5", "extra_quantity": "2"},
            {"counted_5", "extra_quantity"},
        ),
        (
            {"counted_5": "1", "extra_item_id": "05", "extra_quantity": "2"},
            {"counted_5", "extra_quantity"},
        ),
        # An item chosen at the foot with no amount is not counted.
        ({"counted_5": "1", "extra_item_id": "5", "extra_quantity": " "}, set()),
        ({"counted_5": "1", "extra_item_id": "6", "extra_quantity": "2"}, set()),
        # What names no item is no item's repeat.
        ({"counted_x": "1", "counted_y": "2", "counted_0": "3"}, set()),
        ({"counted_²": "1", "counted_2": "2"}, set()),
    ],
)
def test_a_count_form_naming_an_item_twice_names_each_field(
    form: dict[str, str], twice: set[str]
) -> None:
    assert named_twice(lines(form)).keys() == twice


def test_a_count_form_reads_the_item_each_field_names() -> None:
    form = {
        "key": "k",
        "counted_07": "1",
        "counted_x": "2",
        f"counted_{INTEGER_MAX + 1}": "3",
        "extra_item_id": "8",
        "extra_quantity": "4",
    }
    assert lines(form) == [
        ("counted_07", 7, "1"),
        ("counted_x", None, "2"),
        (f"counted_{INTEGER_MAX + 1}", None, "3"),
        ("extra_quantity", 8, "4"),
    ]
    assert named_twice([("counted_5", 5, "1"), ("counted_05", 5, "2")]) == {
        "counted_5": 5,
        "counted_05": 5,
    }
