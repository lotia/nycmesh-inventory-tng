"""What the batch endpoint (POST /api/transactions) accepts, as its schema
says, kept in tests/batch_schema.json, so a change that makes it accept
less fails test_batch_schema, naming each way it does, rather than having a
batch a phone holds from an older build of the scan page refused, and the
volunteer's work lost (CONTRIBUTING.md, The scan page: expand, then
contract; DEPLOYMENT.md, Tightening what a batch accepts).

The file keeps the body's schema, every model it reaches, the endpoint's
parameters, and OLDEST_BUILD, without titles or descriptions, which accept
nothing. Run from backend/, to write it again, or with --check to compare
only, writing nothing, and exit 1 with the difference if it is stale:

    uv run python -m tests.helpers.batch_schema [--check] [--tighten]

`mise run regenerate-batch-schema` runs the first. Over a change that
accepts less, it writes nothing unless OLDEST_BUILD has moved since the
file was written, or --tighten says that no older build's batch is
refused by it.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Literal, cast

from inventory_tng import openapi, page_builds

from . import committed

KEPT = Path(__file__).parent.parent / "batch_schema.json"
URL = "/api/transactions"
COMMANDS = (
    "mise run regenerate-batch-schema, or from backend/: "
    "uv run python -m tests.helpers.batch_schema"
)
CHECKLIST = (
    "Expand, then contract (CONTRIBUTING.md, The scan page): keep accepting "
    "what the build before sends, and accept less only in a later deploy, "
    "after the one whose page sends what the new rule wants is live in "
    "production. That later change sets OLDEST_BUILD in "
    "backend/src/inventory_tng/page_builds.py to the build of the deploy "
    "before it: follow DEPLOYMENT.md, Tightening what a batch accepts."
)

Schema = dict[str, Any]
# Keywords that say what a value is for, never whether it is taken.
ANNOTATIONS = {"title", "description", "examples", "deprecated", "$comment"}
# Keywords whose value is a map of names to schemas, or data, not a schema.
MAPS = {"properties", "patternProperties", "$defs", "dependentSchemas"}
DATA = {"enum", "const", "default", "required"}
# Data whose order says nothing, sorted so reordering a model's fields or a
# Literal's values leaves the file as it was (a parameter's required is
# true or false, not a list).
UNORDERED = {"enum", "required"}
# What compare() reads itself; any other keyword new or changed is taken
# to accept less, not being understood here.
HANDLED = (
    ANNOTATIONS
    | {"$ref", "anyOf", "oneOf", "type", "enum", "const", "default", "required"}
    | {"properties", "additionalProperties", "items"}
    | {"minimum", "exclusiveMinimum", "maximum", "exclusiveMaximum", "multipleOf"}
    | {"minLength", "minItems", "minProperties", "uniqueItems"}
    | {"maxLength", "maxItems", "maxProperties"}
)


def bare(node: object) -> object:
    """`node` without its annotations, and with its unordered lists sorted:
    a property called title is kept."""
    if isinstance(node, list):
        return [bare(each) for each in node]
    if not isinstance(node, dict):
        return node
    return {
        key: bare_value(key, value)
        for key, value in node.items()
        if key not in ANNOTATIONS
    }


def bare_value(key: str, value: object) -> object:
    if key in MAPS and isinstance(value, dict):
        return {name: bare(each) for name, each in value.items()}
    if key in UNORDERED and isinstance(value, list):
        return sorted(value, key=canonical)
    if key in DATA:
        return value
    return bare(value)


def canonical(node: object) -> str:
    """`node` as JSON, the same whatever the order of its keys."""
    return json.dumps(node, sort_keys=True)


def snapshot() -> Schema:
    """What the file keeps, as the code is now."""
    schema = openapi.schema()
    models = schema["components"]["schemas"]
    operation = schema["paths"][URL]["post"]
    body = operation["requestBody"]["content"]["application/json"]["schema"]
    parameters = [
        {key: each[key] for key in ("in", "name", "required", "schema") if key in each}
        for each in operation.get("parameters", ())
    ]
    reached: set[str] = set()

    def reach(node: object) -> None:
        if isinstance(node, list):
            for each in node:
                reach(each)
        elif isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and (name := name_of(ref)) not in reached:
                reached.add(name)
                reach(models[name])
            for value in node.values():
                reach(value)

    reach([body, parameters])
    return cast(
        Schema,
        bare(
            {
                "about": f"What {URL} accepts. Written by {COMMANDS}.",
                "body": body,
                "models": {name: models[name] for name in sorted(reached)},
                "oldest_build": page_builds.OLDEST_BUILD,
                "parameters": parameters,
            }
        ),
    )


def name_of(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def text(snapshot: Schema) -> str:
    return json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def kept() -> Schema:
    return json.loads(KEPT.read_text(encoding="utf-8")) if KEPT.exists() else {}


def tightenings(old: Schema, new: Schema) -> list[str]:
    """Each way `new` accepts less than `old`, as "where: what", each once:
    a batch `old` took that `new` refuses. Erring that way: a keyword not
    read here, new or changed, is one, as is a new pattern or format."""
    return list(dict.fromkeys(Comparison(old, new).found()))


class Comparison:
    def __init__(self, old: Schema, new: Schema) -> None:
        self.old, self.new = old, new
        # Each pair of models compared, with what is said beside each name,
        # and what was found.
        self.done: dict[tuple[str, str], list[str]] = {}

    def found(self) -> list[str]:
        old, new = self.old, self.new
        found = self.compare(old.get("body", {}), new.get("body", {}), "batch")
        before = {
            (each["in"], each["name"]): each for each in old.get("parameters", ())
        }
        for each in new.get("parameters", ()):
            where = f"{each['in']} {each['name']}"
            was = before.get((each["in"], each["name"]))
            if each.get("required") and not (was and was.get("required")):
                found.append(f"{where}: now required")
            if was:
                found += self.compare(
                    was.get("schema", {}), each.get("schema", {}), where
                )
        return found

    def compare(self, was: Schema, now: Schema, where: str) -> list[str]:
        if not now:
            return []  # It takes anything.
        if "$ref" in was and "$ref" in now:
            pair = (canonical(was), canonical(now))
            if pair not in self.done:
                self.done[pair] = []  # Taken as alike while compared: a cycle.
                self.done[pair] = self.compare(
                    resolve(self.old, was), resolve(self.new, now), where
                )
            return self.done[pair]
        if "$ref" in was:
            return self.compare(resolve(self.old, was), now, where)
        if "$ref" in now:
            return self.compare(was, resolve(self.new, now), where)
        took, options = alternatives(was), alternatives(now)
        if len(took) == len(options) == 1:
            if took[0] is was and options[0] is now:
                return self.single(was, now, where)
            return self.compare(took[0], options[0], where)
        # Each alternative it took is taken whole by one it takes now.
        found: list[str] = []
        for each in took:
            tried = [self.compare(each, option, where) for option in options]
            if all(tried):
                kinds = types(resolve(self.old, each))
                alike = [
                    problems
                    for problems, option in zip(tried, options, strict=True)
                    if takes_types(resolve(self.new, option), kinds)
                ]
                found += (
                    alike[0] if alike else [f"{where}: no longer takes {shown(kinds)}"]
                )
        return found

    def single(self, was: Schema, now: Schema, where: str) -> list[str]:
        found: list[str] = []
        if not takes_types(now, types(was)):
            found.append(
                f"{where}: now only {shown(types(now))}, was {shown(types(was))}"
            )
        found += values(was, now, where)
        found += bounds(was, now, where)
        found += self.fields(was, now, where)
        if "items" in was or "items" in now:
            found += self.compare(
                was.get("items", {}), now.get("items", {}), f"{where}[]"
            )
        for key in sorted(now.keys() - HANDLED):
            if now[key] != was.get(key):
                found.append(f"{where}: {key} now {now[key]!r}, was {was.get(key)!r}")
        return found

    def fields(self, was: Schema, now: Schema, where: str) -> list[str]:
        """An object's: one now required, one no longer taken, or what one
        takes."""
        found = [
            f"{where}.{field}: now required"
            for field in now.get("required", ())
            if field not in was.get("required", ())
        ]
        others_were, others = unnamed(was), unnamed(now)
        properties = now.get("properties", {})
        for field, schema in was.get("properties", {}).items():
            if field in properties:
                found += self.compare(schema, properties[field], f"{where}.{field}")
            elif others is False:
                found.append(f"{where}.{field}: no longer taken, nor any field unnamed")
            else:
                # Taken now only as any field it does not name is.
                found += self.compare(schema, others, f"{where}.{field}")
        if others_were is not False:  # Else none it did not name was taken.
            if others is False:
                found.append(f"{where}: a field it does not name now refused")
            else:
                found += self.compare(others_were, others, f"{where}.*")
        return found


def unnamed(node: Schema) -> Schema | Literal[False]:
    """What `node` takes as a field it does not name: a schema ({} for
    anything), or False for none."""
    others = node.get("additionalProperties", {})
    return {} if others is True else others


def resolve(snapshot: Schema, node: Schema) -> Schema:
    """`node`, with the model it names in its place, if it names one."""
    if "$ref" not in node:
        return node
    model = snapshot["models"][name_of(node["$ref"])]
    return {**model, **{key: v for key, v in node.items() if key != "$ref"}}


def alternatives(node: Schema) -> list[Schema]:
    """What `node` takes any of, each with what `node` says beside them: a
    list of types is one each."""
    for key in ("anyOf", "oneOf"):
        if key in node:
            beside = {k: v for k, v in node.items() if k != key}
            return [{**beside, **each} for each in node[key]]
    if isinstance(node.get("type"), list):
        return [{**node, "type": kind} for kind in node["type"]]
    return [node]


def types(node: Schema) -> set[str] | None:
    """The JSON types `node` takes, or None for any."""
    if "type" not in node:
        return None
    kind = node["type"]
    return {kind} if isinstance(kind, str) else set(kind)


def takes_types(node: Schema, wanted: set[str] | None) -> bool:
    taken = types(node)
    if taken is None:
        return True
    if "number" in taken:
        taken = taken | {"integer"}
    return wanted is not None and wanted <= taken


def shown(kinds: set[str] | None) -> str:
    return " or ".join(sorted(kinds)) if kinds else "anything"


def values(was: Schema, now: Schema, where: str) -> list[str]:
    """An enum or const: a value no longer taken."""
    allowed = listed(now)
    if allowed is None:
        return []
    before = listed(was)
    if before is None:
        return [f"{where}: now only {allowed!r}"]
    gone = [each for each in before if each not in allowed]
    return [f"{where}: no longer takes {gone!r}"] if gone else []


def listed(node: Schema) -> list[Any] | None:
    if "const" in node:
        return [node["const"]]
    return node.get("enum")


def bounds(was: Schema, now: Schema, where: str) -> list[str]:
    """Numbers, lengths, counts: each limit new or narrower."""
    found: list[str] = []
    for side, keys in (
        ("lowest", ("minimum", "exclusiveMinimum")),
        ("highest", ("maximum", "exclusiveMaximum")),
    ):
        then, limit = limit_of(was, *keys), limit_of(now, *keys)
        if limit is not None and (then is None or tighter(side, limit, then)):
            found.append(f"{where}: {side} {described(limit)}, was {described(then)}")
    for key in ("minLength", "minItems", "minProperties"):
        if key in now and now[key] > was.get(key, 0):
            found.append(f"{where}: {key} {now[key]}, was {was.get(key, 0)}")
    for key in ("maxLength", "maxItems", "maxProperties"):
        if key in now and (key not in was or now[key] < was[key]):
            found.append(f"{where}: {key} {now[key]}, was {was.get(key)}")
    step = now.get("multipleOf")
    if step is not None and ("multipleOf" not in was or was["multipleOf"] % step):
        found.append(f"{where}: multipleOf {step}, was {was.get('multipleOf')}")
    if now.get("uniqueItems") and not was.get("uniqueItems"):
        found.append(f"{where}: uniqueItems now")
    return found


# A limit: its value, and whether the value itself is refused.
Limit = tuple[float, bool]


def limit_of(node: Schema, inclusive: str, exclusive: str) -> Limit | None:
    """The stricter of node's two limits on a side, if it has one."""
    found = [(node[inclusive], False)] if inclusive in node else []
    found += [(node[exclusive], True)] if exclusive in node else []
    if not found:
        return None
    lowest = inclusive == "minimum"
    return max(found, key=lambda each: (each[0] if lowest else -each[0], each[1]))


def tighter(side: str, now: Limit, was: Limit) -> bool:
    if now[0] != was[0]:
        return now[0] > was[0] if side == "lowest" else now[0] < was[0]
    return now[1] and not was[1]


def described(limit: Limit | None) -> str:
    if limit is None:
        return "none"
    return f"{limit[0]} {'refused' if limit[1] else 'taken'}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--check", action="store_true", help="compare only, and write nothing"
    )
    parser.add_argument(
        "--tighten",
        action="store_true",
        help="write it although the batch accepts less and OLDEST_BUILD is "
        "unchanged: no older build's batch is refused by it",
    )
    arguments = parser.parse_args(argv)
    now = snapshot()
    if arguments.check:
        return committed.check(KEPT, text(now))
    was = kept()
    if was and was.get("oldest_build") == now["oldest_build"] and not arguments.tighten:
        found = tightenings(was, now)
        if found:
            print("The batch endpoint accepts less:", *found, sep="\n- ")
            print(f"\n{CHECKLIST}\nWrote nothing: OLDEST_BUILD is unchanged.")
            return 1
    return committed.write(KEPT, text(now))


if __name__ == "__main__":
    sys.exit(main())
