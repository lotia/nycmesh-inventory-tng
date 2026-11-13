"""The batch endpoint accepts all it did when tests/batch_schema.json was
written, so a change that makes it accept less, which would have a batch a
phone holds from an older build of the scan page refused, fails here,
naming each way, until the deploy follows expand, then contract
(CONTRIBUTING.md, The scan page; DEPLOYMENT.md, Tightening what a batch
accepts)."""

import copy
from collections.abc import Callable
from pathlib import Path

import pytest

from inventory_tng import page_builds

from helpers import batch_schema, committed
from helpers.batch_schema import (
    CHECKLIST,
    COMMANDS,
    KEPT,
    Schema,
    bare,
    kept,
    main,
    snapshot,
    text,
    tightenings,
)


@pytest.fixture(scope="module")
def now() -> Schema:
    """Built once: each build makes a whole app."""
    return snapshot()


def test_the_batch_accepts_all_it_did(now: Schema) -> None:
    """Rules inside validators are not in the schema, so not seen here
    (CONTRIBUTING.md, The scan page)."""
    found = tightenings(kept(), now)
    assert not found, (
        "The batch endpoint accepts less than it did:\n- "
        + "\n- ".join(found)
        + f"\n\n{CHECKLIST} Then write {KEPT.name} again ({COMMANDS}), which "
        "refuses while OLDEST_BUILD is unchanged, unless given --tighten for "
        "a change no older build's batch meets: a field required only inside "
        "an object that is itself new and optional, say."
    )
    stale = committed.difference(KEPT, text(now))
    assert stale == "", (
        f"The batch endpoint accepts more than {KEPT.name} says, or OLDEST_BUILD "
        f"moved: write it again ({COMMANDS}), so that a later change accepting "
        f"less is measured against what is deployed:\n{stale}"
    )


def field(schema: Schema, model: str, name: str) -> Schema:
    return schema["models"][model]["properties"][name]


def first(schema: Schema) -> Schema:
    """The first alternative of an optional field: what it takes but null."""
    return schema["anyOf"][0]


Change = Callable[[Schema], object]
# Each change, and what is said of it: a line for each way.
TIGHTENINGS: dict[str, tuple[Change, str]] = {
    "a field required": (
        lambda s: s["models"]["Batch"]["required"].append("to_location_id"),
        "batch.to_location_id: now required",
    ),
    "a new field required": (
        lambda s: (
            s["models"]["Line"]["properties"].update(note={"type": "string"}),
            s["models"]["Line"]["required"].append("note"),
        ),
        "batch.lines[].note: now required",
    ),
    "a lowest number raised": (
        lambda s: field(s, "Batch", "volunteer_id").update(exclusiveMinimum=10),
        "batch.volunteer_id: lowest 10 refused, was 0.0 refused",
    ),
    "the lowest number refused": (
        lambda s: first(field(s, "LengthReport", "left")).update(exclusiveMinimum=0),
        "batch.lengths[].left: lowest 0 refused, was 0.0 taken",
    ),
    "a highest number lowered": (
        lambda s: field(s, "Batch", "volunteer_id").update(maximum=1000),
        "batch.volunteer_id: highest 1000 taken, was 2147483647.0 taken",
    ),
    "a highest number new": (
        lambda s: first(field(s, "Line", "quantity")).update(maximum=1000),
        "batch.lines[].quantity: highest 1000 taken, was none",
    ),
    "a multiple": (
        lambda s: first(field(s, "Line", "quantity")).update(multipleOf=0.5),
        "batch.lines[].quantity: multipleOf 0.5, was None",
    ),
    "fewer lines": (
        lambda s: field(s, "Batch", "lines").update(maxItems=100),
        "batch.lines: maxItems 100, was 500",
    ),
    "some lines": (
        lambda s: field(s, "Batch", "lines").update(minItems=1),
        "batch.lines: minItems 1, was 0",
    ),
    "a longest text": (
        lambda s: field(s, "Line", "quantity")["anyOf"][1].update(maxLength=20),
        "batch.lines[].quantity: maxLength 20, was None",
    ),
    "a shortest text": (
        lambda s: field(s, "Line", "quantity")["anyOf"][1].update(minLength=1),
        "batch.lines[].quantity: minLength 1, was 0",
    ),
    "a pattern": (
        lambda s: field(s, "Line", "quantity")["anyOf"][1].update(pattern="^[0-9.]+$"),
        "batch.lines[].quantity: pattern now '^[0-9.]+$', was None",
    ),
    "a format": (
        lambda s: field(s, "Batch", "key").update(format="uri"),
        "batch.key: format now 'uri', was 'uuid'",
    ),
    "an enum value gone": (
        lambda s: field(s, "Batch", "kind")["enum"].remove("report"),
        "batch.kind: no longer takes ['report']",
    ),
    "a literal": (
        lambda s: field(s, "Batch", "kind").update(const="move"),
        "batch.kind: no longer takes ['install', 'open', 'receipt', 'report']",
    ),
    "an enum new": (
        lambda s: first(field(s, "Line", "whole")).update(enum=[True]),
        "batch.lines[].whole: now only [True]",
    ),
    "null no longer taken": (
        lambda s: s["models"]["Batch"]["properties"].update(
            from_location_id=first(field(s, "Batch", "from_location_id"))
        ),
        "batch.from_location_id: no longer takes null",
    ),
    "a type narrowed": (
        lambda s: first(field(s, "Line", "quantity")).update(type="integer"),
        "batch.lines[].quantity: no longer takes number",
    ),
    "an alternative gone": (
        lambda s: s["models"]["LengthReport"]["properties"].update(
            left=first(field(s, "LengthReport", "left"))
        ),
        "batch.lengths[].left: no longer takes string",
    ),
    "other fields refused": (
        lambda s: s["models"]["Line"].update(additionalProperties=False),
        "batch.lines[]: a field it does not name now refused",
    ),
    "a field gone, others refused": (
        lambda s: (
            s["models"]["LengthReport"].update(additionalProperties=False),
            s["models"]["LengthReport"]["properties"].pop("left"),
            s["models"]["LengthReport"]["required"].remove("left"),
        ),
        (
            "batch.lengths[].left: no longer taken, nor any field unnamed\n"
            "batch.lengths[]: a field it does not name now refused"
        ),
    ),
    "repeats refused": (
        lambda s: first(field(s, "Batch", "used_up")).update(uniqueItems=True),
        "batch.used_up: uniqueItems now",
    ),
    "a keyword not read here": (
        lambda s: field(s, "Batch", "kind").update(**{"not": {"const": "move"}}),
        "batch.kind: not now {'const': 'move'}, was None",
    ),
    "a header required": (
        lambda s: s["parameters"][0].update(required=True),
        "header X-Inventory-Build: now required",
    ),
    "a header's value narrowed": (
        lambda s: s["parameters"][0].update(schema={"type": "string"}),
        "header X-Inventory-Build: no longer takes null",
    ),
    "one alternative, alone, narrower": (
        lambda s: field(s, "Batch", "lines").update(anyOf=[{"maxItems": 100}]),
        "batch.lines: maxItems 100, was 500",
    ),
    "a list of types narrowed": (
        lambda s: field(s, "Batch", "volunteer_id").update(type=["null"]),
        "batch.volunteer_id: now only null, was integer",
    ),
    "a field gone, others taken only as one kind": (
        lambda s: (
            s["models"]["LengthReport"].update(additionalProperties={"type": "string"}),
            s["models"]["LengthReport"]["properties"].pop("left"),
            s["models"]["LengthReport"]["required"].remove("left"),
        ),
        (
            "batch.lengths[].left: no longer takes number\n"
            "batch.lengths[].*: now only string, was anything"
        ),
    ),
    "a model it names changed": (
        lambda s: s["models"]["LengthReport"]["properties"]["open_package_key"].update(
            maxLength=36
        ),
        "batch.lengths[].open_package_key: maxLength 36, was None",
    ),
}


@pytest.mark.parametrize(("change", "said"), TIGHTENINGS.values(), ids=TIGHTENINGS)
def test_each_way_of_accepting_less_is_found(
    now: Schema, change: Change, said: str
) -> None:
    changed = copy.deepcopy(now)
    change(changed)
    assert "\n".join(tightenings(now, changed)) == said


LOOSENINGS: dict[str, Change] = {
    "a new field, optional": lambda s: s["models"]["Line"]["properties"].update(
        note={"type": "string"}
    ),
    "a field no longer required": lambda s: s["models"]["Line"]["required"].remove(
        "quantity"
    ),
    "a field gone, others ignored": lambda s: s["models"]["Line"]["properties"].pop(
        "whole"
    ),
    "a new object, optional, with a field it requires": lambda s: s["models"]["Batch"][
        "properties"
    ].update(
        note={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        }
    ),
    "more lines": lambda s: field(s, "Batch", "lines").update(maxItems=1000),
    "a limit gone": lambda s: field(s, "Batch", "volunteer_id").pop("maximum"),
    "a lower number taken": lambda s: field(s, "Batch", "volunteer_id").update(
        exclusiveMinimum=-1
    ),
    "an enum value new": lambda s: field(s, "Batch", "kind")["enum"].append("lend"),
    "null taken": lambda s: field(s, "Batch", "volunteer_id").update(
        type=["integer", "null"]
    ),
    "a list of types as alternatives": lambda s: s["parameters"][0].update(
        schema={"type": ["string", "null"]}
    ),
    "one alternative, alone": lambda s: field(s, "Batch", "lines").update(
        anyOf=[{"maxItems": 600}]
    ),
    "a whole number now any": lambda s: field(s, "Batch", "volunteer_id").update(
        type="number"
    ),
    "a format gone": lambda s: field(s, "Batch", "key").pop("format"),
    "a header new, optional": lambda s: s["parameters"].append(
        {"in": "header", "name": "X-Other", "required": False, "schema": {}}
    ),
    "OLDEST_BUILD moved": lambda s: s.update(oldest_build="2026-11-12T10:00:00.000Z"),
}


@pytest.mark.parametrize("change", LOOSENINGS.values(), ids=LOOSENINGS)
def test_accepting_more_is_not_accepting_less(now: Schema, change: Change) -> None:
    changed = copy.deepcopy(now)
    change(changed)
    assert changed != now
    assert tightenings(now, changed) == []


def test_a_model_naming_itself_is_compared_once() -> None:
    def tree(maximum: int) -> Schema:
        node = {
            "type": "object",
            "properties": {
                "size": {"type": "integer", "maximum": maximum},
                "children": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/Node"},
                },
            },
        }
        return {"body": {"$ref": "#/components/schemas/Node"}, "models": {"Node": node}}

    assert tightenings(tree(10), tree(10)) == []
    assert tightenings(tree(10), tree(5)) == [
        "batch.size: highest 5 taken, was 10 taken"
    ]


def test_other_fields_once_refused_and_now_taken_is_accepting_more(
    now: Schema,
) -> None:
    refused = copy.deepcopy(now)
    refused["models"]["Line"]["additionalProperties"] = False
    taken = copy.deepcopy(now)
    taken["models"]["Line"]["additionalProperties"] = {"type": "string"}
    assert tightenings(refused, taken) == []


def test_a_model_reached_twice_is_said_once_and_what_is_beside_it_read() -> None:
    def two(beside: Schema) -> Schema:
        ref = {"$ref": "#/components/schemas/Size"}
        return {
            "body": {
                "type": "object",
                "properties": {"a": ref, "b": {**ref, **beside}, "c": ref},
            },
            "models": {"Size": {"type": "integer", "maximum": 10}},
        }

    assert tightenings(two({}), two({})) == []
    smaller = two({"maximum": 5})
    smaller["models"]["Size"]["maximum"] = 8
    assert tightenings(two({}), smaller) == [
        "batch.a: highest 8 taken, was 10 taken",
        "batch.b: highest 5 taken, was 10 taken",
    ]


def test_order_that_says_nothing_is_not_kept() -> None:
    """Reordering a model's fields or a Literal's values writes the same."""
    assert bare({"required": ["b", "a"], "enum": ["b", None, 1, "a"]}) == {
        "required": ["a", "b"],
        "enum": ["a", "b", 1, None],
    }


def test_only_what_accepts_nothing_is_left_out() -> None:
    """A field may be called title: only a schema's own annotations go."""
    assert bare(
        {
            "title": "Line",
            "description": "One line",
            "properties": {"title": {"type": "string", "title": "Title"}},
            "enum": [{"title": "kept, a value"}],
        }
    ) == {
        "properties": {"title": {"type": "string"}},
        "enum": [{"title": "kept, a value"}],
    }


def test_the_file_is_the_batch_its_models_and_the_oldest_build(now: Schema) -> None:
    assert now["body"] == {"$ref": "#/components/schemas/Batch"}
    assert sorted(now["models"]) == ["Batch", "LengthReport", "Line"]
    assert now["oldest_build"] == page_builds.OLDEST_BUILD
    assert "description" not in field(now, "Batch", "lines")


@pytest.fixture
def stale(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, now: Schema) -> Path:
    """The file, written before the batch's lines were capped at 500, so
    the batch now accepts less."""
    was = copy.deepcopy(now)
    field(was, "Batch", "lines")["maxItems"] = 1000
    path = tmp_path / "batch_schema.json"
    path.write_text(text(was), encoding="utf-8")
    monkeypatch.setattr(batch_schema, "KEPT", path)
    return path


def test_check_writes_nothing_and_fails_while_stale(
    stale: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = stale.read_text(encoding="utf-8")
    assert main(["--check"]) == 1
    assert '-          "maxItems": 1000,' in capsys.readouterr().out
    assert stale.read_text(encoding="utf-8") == before


def test_accepting_less_is_written_only_with_oldest_build_moved_or_tighten(
    stale: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    before = stale.read_text(encoding="utf-8")
    assert main([]) == 1
    said = capsys.readouterr().out
    assert "- batch.lines: maxItems 500, was 1000" in said
    assert "Wrote nothing: OLDEST_BUILD is unchanged." in said
    assert stale.read_text(encoding="utf-8") == before
    assert main(["--tighten"]) == 0
    assert main(["--check"]) == 0
    assert "is up to date" in capsys.readouterr().out

    stale.write_text(before, encoding="utf-8")
    monkeypatch.setattr(page_builds, "OLDEST_BUILD", "2026-11-12T10:00:00.000Z")
    assert main([]) == 0
    assert kept()["oldest_build"] == "2026-11-12T10:00:00.000Z"


def test_with_no_file_it_is_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "batch_schema.json"
    monkeypatch.setattr(batch_schema, "KEPT", path)
    assert main([]) == 0
    assert kept() == snapshot()
