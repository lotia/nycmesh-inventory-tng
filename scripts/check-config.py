"""Configuration values nobody explained, and values nobody documented.

Two different faults, and only the second is about a reader of the config file.

A value with no prose beside it is guesswork for whoever meets it: `debug:
false` says what it is set to and nothing about what turning it on costs. And a
variable the chart renders into a running container but `docs/deployment.md`
never mentions is worse, because an operator reading the document that lists
what they may set has no way to learn it exists. That second one is what found
`CLIENT_REPORT_RATE`, which was set in `.env.sample`, rendered by the chart, and
absent from the table while every one of its sibling rate limits had a row.

WHAT COUNTS AS DOCUMENTED, and getting this wrong is the whole difficulty. The
naive rule -- a comment on the line immediately above -- reports three times as
many failures as exist and every one of them is spurious. `.env.sample` groups
related variables under one comment that names each in turn, and that is better
documentation than a comment per line rather than worse. So the rule here is
the one a reader actually uses: a comment block directly above the CONTIGUOUS
GROUP a value belongs to, with no blank line breaking the two apart. In YAML a
comment above an enclosing key covers what is nested beneath it, for the same
reason -- the block comment on `resources:` explains its `requests:` and
`limits:` and does not need repeating on each leaf.

WHY THIS READS TEXT RATHER THAN PARSING IT. Comments are not in a parsed tree,
which is the obvious half. The half worth writing down is that PyYAML is a
`backend/` dependency and not a `scripts/` one, and the two CI jobs that run
this checker install nothing before doing so -- so importing it would break
them. `check-docs.py` and `check-telemetry.py` beside this are stdlib-only for
the same reason, and a future reader who notices PyYAML is available in the
backend's tests has an obvious-looking simplification that does not work here.

WHAT THIS DELIBERATELY DOES NOT READ. Whether the prose is any good. It cannot,
and a checker that pretended to would be worse than one that admits the limit:
a line saying "# the port" above `port:` passes here and helps nobody. What it
holds is that somebody was asked the question at all.
"""

import re
import sys
from pathlib import Path

# `KEY=value`, which is every assignment in an environment file.
ENV_ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]*)=")
# An environment entry inside compose.yaml's six-space `environment:` blocks.
COMPOSE_ENTRY = re.compile(r"^\s{6}([A-Z][A-Z0-9_]*):\s")
# A YAML key with a value on the same line: a leaf rather than a parent.
YAML_LEAF = re.compile(r"^(\s*)([a-zA-Z][a-zA-Z0-9_]*):\s*(\S.*)$")
# A YAML key with nothing after it: a parent, whose comment covers its children.
YAML_PARENT = re.compile(r"^(\s*)([a-zA-Z][a-zA-Z0-9_]*):\s*$")
# What the chart puts in a container's environment.
#
# Leading whitespace is tolerated, and the count is checked below. Anchored at
# column zero this matched nothing the moment the helper was indented -- by a
# reformat, or a `range` wrapped around the block -- and a rule that matches
# nothing reports nothing and passes. That is the one failure a checker may not
# have, so the shape of the file is not allowed to decide whether it runs.
CHART_RENDERS = re.compile(r"^\s*-\s*name:\s*([A-Z][A-Z0-9_]*)\s*$", re.MULTILINE)


def commented_above(lines: list[str], start: int) -> bool:
    """Is there a comment block directly above the line at ``start``?"""
    above = start - 1
    return above >= 0 and lines[above].lstrip().startswith("#")


def flat_values(lines: list[str], pattern: re.Pattern[str]) -> list[tuple[str, int, bool]]:
    """Every match, grouped by adjacency, with whether its group is explained.

    Adjacent assignments are one group and share whatever comment sits above
    the first of them, which is how these files are written and how they read.
    """
    found: list[tuple[str, int, bool]] = []
    line = 0
    while line < len(lines):
        if not pattern.match(lines[line]):
            line += 1
            continue
        group: list[tuple[str, int]] = []
        end = line
        while end < len(lines):
            match = pattern.match(lines[end])
            if not match:
                break
            group.append((match.group(1), end + 1))
            end += 1
        explained = commented_above(lines, line)
        found.extend((name, number, explained) for name, number in group)
        line = end
    return found


def yaml_values(lines: list[str]) -> list[tuple[str, int, bool]]:
    """Every leaf value in a YAML document, and whether anything explains it.

    A leaf is explained by a comment above its own group, or by one above any
    key it is nested under -- so a block comment on `resources:` covers the
    figures beneath it without being repeated on each.

    Named by its PATH rather than its key, so that `backend.port` and
    `frontend.port` are two values rather than one seen twice. A leaf name is
    not unique -- `memory` occurs four times in this chart -- and an allowlist
    keyed on the bare name excused every one of them from a line written about
    one.

    THE BACKWARD WALK IS NOT REDUNDANT WITH THE FORWARD ONE, which is worth
    saying because it looks it. A simplify pass proposed carrying "is this
    explained" on the same stack that builds the path, on the grounds that a
    comment shared across adjacent sibling leaves does not occur here. It does:
    `nameOverride` and `fullnameOverride` share one, and so do `registry` and
    `repository`. Collapsing the two passes made this repository's own chart
    fail, which is how the claim was found to be wrong.
    """
    found: list[tuple[str, int, bool]] = []
    enclosing: list[tuple[int, str]] = []
    for line, text in enumerate(lines):
        parent = YAML_PARENT.match(text)
        if parent:
            depth = len(parent.group(1))
            enclosing = [(at, key) for at, key in enclosing if at < depth]
            enclosing.append((depth, parent.group(2)))
            continue
        leaf = YAML_LEAF.match(text)
        if not leaf or leaf.group(3).startswith("#"):
            continue
        depth = len(leaf.group(1))
        above = [key for at, key in enclosing if at < depth]
        path = ".".join([*above, leaf.group(2)])
        found.append((path, line + 1, explained_here_or_above(lines, line)))
    return found


def explained_here_or_above(lines: list[str], leaf: int) -> bool:
    """Walk out of the nesting, looking for a comment over anything enclosing."""
    indent = len(lines[leaf]) - len(lines[leaf].lstrip())
    line = leaf - 1
    while line >= 0:
        text = lines[line]
        stripped = text.strip()
        if stripped.startswith("#"):
            return True
        if not stripped:
            # A blank line ends this group. The enclosing key may still carry a
            # comment, so climb rather than give up -- but only outwards.
            line = outer_key(lines, line, indent)
            if line is None:
                return False
            indent = len(lines[line]) - len(lines[line].lstrip())
            continue
        here = len(text) - len(text.lstrip())
        if here < indent and YAML_PARENT.match(text):
            indent = here
            line -= 1
            continue
        if here < indent:
            return False
        line -= 1
    return False


def outer_key(lines: list[str], line: int, indent: int) -> int | None:
    """The nearest enclosing parent above ``line``, if there is one."""
    while line >= 0:
        text = lines[line]
        if text.strip() and YAML_PARENT.match(text):
            here = len(text) - len(text.lstrip())
            if here < indent:
                return line
        line -= 1
    return None


def allowed(root: Path) -> dict[str, str]:
    """Values excused from needing prose, and the reason each was excused.

    Keyed by FILE and name rather than by name alone, which is how the sibling
    allowlists key on a path and is not merely tidiness here: a YAML leaf name
    is not unique -- `memory` appears four times in the chart today and `port`
    twice -- so a bare name silenced every value sharing it, across all four
    surfaces, from one line written about one of them.
    """
    path = root / "scripts" / "check-config.allow"
    if not path.exists():
        return {}
    entries: dict[str, str] = {}
    for text in path.read_text().splitlines():
        if not text.strip() or text.lstrip().startswith("#"):
            continue
        where, _, rest = text.partition(":")
        name, _, why = rest.partition(":")
        if why.strip():
            entries[f"{where.strip()}:{name.strip()}"] = why.strip()
    return entries


def readable(path: Path, root: Path) -> str:
    """The text of a file this must read, or a refusal naming it.

    Every sibling checker names the thing it could not find rather than dying
    with a stack, because "the checker is broken" is not something to work out
    from a traceback -- and a surface that has been renamed is exactly when
    somebody needs telling which one.
    """
    try:
        return path.read_text()
    except OSError as gone:
        raise SystemExit(f"check-config: cannot read {path.relative_to(root)} -- {gone.strerror}.") from gone


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    excused = allowed(root)
    # `fail` and `note` lines, for report.sh to draw. The glyph, the count and
    # the closing verdict all live there and are shared by every checker beside
    # this one -- its own header records what happened the last time three
    # scripts each kept a copy.
    objected = False

    def report(where: str, name: str, number: int, detail: str) -> None:
        nonlocal objected
        objected = True
        print(f"fail {where}:{number}  {name} {detail}")

    surfaces: list[tuple[str, list[tuple[str, int, bool]]]] = []
    env_sample = root / ".env.sample"
    surfaces.append((".env.sample", flat_values(readable(env_sample, root).splitlines(), ENV_ASSIGNMENT)))
    compose = root / "compose.yaml"
    surfaces.append(("compose.yaml", flat_values(readable(compose, root).splitlines(), COMPOSE_ENTRY)))
    values = root / "infra" / "helm" / "inventory-tng" / "values.yaml"
    surfaces.append((str(values.relative_to(root)), yaml_values(readable(values, root).splitlines())))

    for where, entries in surfaces:
        for name, number, explained in entries:
            if explained or f"{where}:{name}" in excused:
                continue
            report(where, name, number, "is set with nothing saying what it is for")

    # The rule that matters to somebody deploying rather than somebody reading.
    helper = root / "infra" / "helm" / "inventory-tng" / "templates" / "_helpers.tpl"
    document = readable(root / "docs" / "deployment.md", root)
    rendered = sorted(set(CHART_RENDERS.findall(readable(helper, root))))
    if not rendered:
        raise SystemExit(
            f"check-config: found no rendered variables in {helper.relative_to(root)}. "
            "The pattern has stopped matching rather than the chart having stopped "
            "rendering, and a rule matching nothing reports nothing."
        )
    for name in rendered:
        helper_key = f"{helper.relative_to(root)}:{name}"
        if f"`{name}`" in document or helper_key in excused:
            continue
        report(
            str(helper.relative_to(root)),
            name,
            1,
            "is rendered into a container and docs/deployment.md never mentions it",
        )

    # Said once, and only to somebody who has an objection to answer. A hint
    # printed over a clean run is noise that teaches people to skim the output.
    if objected:
        print("note scripts/check-config.allow is for the few that genuinely need no prose,")
        print("note and its header says how an entry is written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
