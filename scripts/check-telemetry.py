"""The reader behind check-telemetry.sh, which is the entry point and names
both the rule and the corpus.

WHAT A CHECKER CAN HONESTLY PROVE, because the obvious assertion proves
nothing. Auto-instrumentation gives every route a span and every query a child
span for free, so "this endpoint has a span" is true of an endpoint nobody has
touched. What is worth enforcing is the deliberate half: that a module which
changes something has a logger of its own, and that each of its state-changing
entry points says something on the way through.

WHAT IS READ, and the two things deliberately outside it:

- Modules defining a DRF view, and management commands. Both change things and
  both are reachable by somebody who is not watching.
- NOT `inventory/sheet/`. Those steps compute a report and change nothing; what
  they counted is said by the command that ran them, which is a command and so
  is read. A logger in each of them would be a logger with nothing to say.
- NOT the models. A model method is called from the paths above, which are
  read, and the query it makes is a span already.

WHAT COUNTS AS SAYING SOMETHING is a call to a logger or to a counter --
`log.info`, `log.warning`, `telemetry.APPENDS.add`, `_telemetry.running`. Not
a particular level and not a particular wording: this is a checker, and the
question of whether a record is any *good* is a review's.

AND A WRITE IS OFTEN NOT DECLARED AT ALL, which is what this had to learn to
see. A DRF generic view is written declaratively -- `class
LocationListView(ListCreateAPIView)` with an empty body handles POST -- and
what speaks for it is a mixin two names along the base list. Reading only the
methods a class states itself found nothing to complain about in the dominant
pattern in this repository, however silent it was: the four list views could
have lost the mixin that speaks for them and this would have printed the
all-clear. So a base is followed. A class inherits a write if any base brings
one, and it has said something if any base written in the same module says it.
A base from another module is out of reach, which is the honest limit and is
part of what the allowlist is for.
"""

import ast
import os
import pathlib
import sys

ALLOW = pathlib.Path(os.environ["ALLOW"])

# Bases that bring a write with them, so a class inheriting one handles a write
# whether or not it declares a line of it.
WRITING_BASES = {
    "ListCreateAPIView",
    "CreateAPIView",
    "UpdateAPIView",
    "DestroyAPIView",
    "RetrieveUpdateAPIView",
    "RetrieveUpdateDestroyAPIView",
    "RetrieveDestroyAPIView",
    "ModelViewSet",
}

# Bases that bring none, so only what the class declares counts. An `APIView`
# with a `get` and nothing else changes nothing.
PLAIN_BASES = {"APIView", "GenericAPIView", "ListAPIView", "RetrieveAPIView"}

# What makes a class a view worth reading at all. Matched by the base's final
# name, so `generics.ListCreateAPIView` and a bare `ListCreateAPIView` are the
# same thing.
VIEW_BASES = WRITING_BASES | PLAIN_BASES

# The methods that change something, grouped by the REQUEST PATH they serve.
# `get` is not one: a read that says nothing is an ordinary read, and requiring
# a record per read would produce a log nobody can find anything in.
#
# GROUPED, because DRF splits one write across two methods: `update` calls
# `perform_update`, `create` calls `perform_create`, and a class declaring both
# is serving one edit rather than two. Asking each method separately for a
# record of its own asks for two records per edit -- and it reported a class
# that had moved its record from one half to the other, which is where the row
# and its fields are already in hand, for the half it left quiet.
CHANGES = {
    "post": "create",
    "create": "create",
    "perform_create": "create",
    "put": "update",
    "patch": "update",
    "update": "update",
    "perform_update": "update",
    "delete": "delete",
    "perform_destroy": "delete",
    "handle": "run",
}

# What a module says with. A logger or a counter -- both are the module
# speaking, and which is right for a given path is a review's question.
SPEAKS = ("log.", "logger.", "telemetry.", "_telemetry.")

# `async def post` is a `FunctionDef`'s sibling rather than a `FunctionDef`, so
# asking only about the latter passed over one in silence.
DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def allowances() -> dict[str, str]:
    """The exceptions, as `path: why`.

    One per line, the path then a reason, which is what makes an exception a
    decision somebody took rather than a gap. A line with no reason is an
    exception nobody argued and is refused.
    """
    allowed: dict[str, str] = {}
    for line in ALLOW.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        path, _, why = line.partition(":")
        allowed[path.strip()] = why.strip()
    return allowed


def says_something(node: ast.AST) -> bool:
    """Whether anything under this node calls a logger or a counter."""
    return any(
        isinstance(held, ast.Call) and any(ast.unparse(held.func).startswith(word) for word in SPEAKS)
        for held in ast.walk(node)
    )


def named(base: ast.expr) -> str:
    """A base's final name, so `generics.ListCreateAPIView` matches a bare one."""
    return ast.unparse(base).rsplit(".", 1)[-1]


def declared(owner: ast.ClassDef) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """The state-changing entry points this class states itself."""
    return [held for held in owner.body if isinstance(held, DEFINITIONS) and held.name in CHANGES]


def classes(tree: ast.Module) -> dict[str, ast.ClassDef]:
    """Every class this module defines, by name, so a base can be followed."""
    return {node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}


def family(owner: ast.ClassDef, defined: dict[str, ast.ClassDef]) -> list[ast.ClassDef]:
    """This class and every base of it written in the same module.

    Cycle-guarded, because a base list is whatever somebody typed rather than
    anything Python has validated at the point this reads it. Handing back the
    classes rather than answering a question about them is what lets the two
    questions below be one line each.
    """
    seen: set[str] = set()
    waiting = [owner]
    found = []
    while waiting:
        node = waiting.pop()
        if node.name in seen:
            continue
        seen.add(node.name)
        found.append(node)
        waiting.extend(defined[named(base)] for base in node.bases if named(base) in defined)
    return found


def writes(owner: ast.ClassDef, defined: dict[str, ast.ClassDef]) -> bool:
    """Whether a request reaching this class can change something."""
    return any(
        declared(node) or any(named(base) in WRITING_BASES for base in node.bases)
        for node in family(owner, defined)
    )


def speaks(owner: ast.ClassDef, defined: dict[str, ast.ClassDef]) -> bool:
    """Whether this class, or a base of it written here, says so on a write."""
    return any(says_something(method) for node in family(owner, defined) for method in declared(node))


def all_bases_are_in_reach(owner: ast.ClassDef, defined: dict[str, ast.ClassDef]) -> bool:
    """Whether every base of this class is one this reader can actually judge.

    THE RULE THAT HAS TO FAIL LOUD. A base written in another module is not
    followed, so a class whose speech comes from such a base looks silent --
    and the previous reading of that was to say nothing, which means moving
    `RecordsWhatItCreated` into a `mixins.py` would have turned four write
    endpoints from a complaint into no complaint at all, with the all-clear
    printed over them. A checker that goes quiet when it stops being able to
    see is worse than no checker.

    So a base that is neither a DRF one this knows nor a class written here is
    a reason to speak up, and the allowlist is where saying "that one is fine"
    goes -- which is a decision somebody wrote down rather than a silence.
    """
    return all(named(base) in VIEW_BASES or named(base) in defined for base in owner.bases)


def has_a_logger(tree: ast.Module) -> bool:
    """Whether the module holds a logger of its own, or shares a named one."""
    return any(
        "get_logger" in ast.unparse(node) or "_telemetry" in ast.unparse(node)
        for node in tree.body
        if isinstance(node, ast.Assign | ast.Import | ast.ImportFrom)
    )


def views(tree: ast.Module) -> list[ast.ClassDef]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and any(named(base) in VIEW_BASES for base in node.bases)
    ]


def quiet(path: pathlib.Path) -> list[str]:
    """What this file changes without saying so."""
    tree = ast.parse(path.read_text())
    defined = classes(tree)
    # A command is a module with a `Command` class, which is what Django looks
    # for -- not every module that happens to sit beside one. The underscored
    # helpers there are steps a command calls, and what they did is said by
    # the command that called them.
    commands = [node for node in defined.values() if node.name == "Command"]
    found = views(tree) or commands
    if not found:
        return []

    complaints = []
    if not has_a_logger(tree):
        complaints.append(f"{path}: changes things and has no logger of its own")
    for owner in found:
        stated = declared(owner)
        serving: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
        for method in stated:
            serving.setdefault(CHANGES[method.name], []).append(method)
        for methods in serving.values():
            if not any(says_something(method) for method in methods):
                # Named for the first of them, so a class declaring only one --
                # which is most of them -- reads exactly as it always did.
                complaints.append(f"{path}: {owner.name}.{methods[0].name} changes something and says nothing")
        # The declarative half: a class with nothing of its own in `CHANGES`
        # still answers a POST when a base hands it one, and then the loop
        # above has nothing to look at.
        if not stated and writes(owner, defined) and not speaks(owner, defined):
            if all_bases_are_in_reach(owner, defined):
                complaints.append(f"{path}: {owner.name} handles a write it does not declare and says nothing about it")
            else:
                out_of_reach = [named(base) for base in owner.bases if named(base) not in {*VIEW_BASES, *defined}]
                complaints.append(
                    f"{path}: {owner.name} handles a write and inherits from {', '.join(out_of_reach)}, "
                    "which is not read here, so whether it says anything cannot be told"
                )
    return complaints


def main() -> int:
    allowed = allowances()
    # WHAT WAS LOOKED AT, and what each of them had to answer for, as one
    # mapping. The distinction matters: judging an allowance against the files
    # that COMPLAINED alone made every allowance for a file outside the
    # arguments a failure, so `check-telemetry.sh <path>` -- the form this
    # tool's own header advertises, and the shape any changed-files hook would
    # take -- failed about a file the caller had not asked about.
    examined = {str(path): quiet(path) for path in map(pathlib.Path, sys.argv[1:])}

    for path, found in examined.items():
        if path not in allowed:
            for line in found:
                print(f"fail {line}")
    for path, why in sorted(allowed.items()):
        if path not in examined:
            continue
        # An allowance nothing matches has outlived what it excused: the module
        # it names has been fixed, or has stopped being read, and either way
        # the line is a licence nobody is using.
        if not examined[path]:
            print(f"fail {path} is allowed to say nothing and has nothing to excuse: {why or 'no reason given'}")
        elif not why:
            print(f"fail {path} is allowed to say nothing and does not say why")
    return 0


if __name__ == "__main__":
    sys.exit(main())
