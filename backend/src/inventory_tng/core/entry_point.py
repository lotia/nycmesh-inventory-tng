"""What the operator's entry point (manage.py) and the developer's
(backend/devtools/__main__.py) share, so they cannot drift (docs/decisions/
0023): a fixed table of commands, each listed by name with a line on what
it does, never discovered; with no command, the list; else only the
chosen command's module is imported, and the rest of the arguments are its
own, `--help` among them. Each module's `main(argv, prog)` returns the
exit status.
"""

import argparse
import importlib
from collections.abc import Mapping, Sequence

# Each command's name: its module, and a line on what it does.
Commands = Mapping[str, tuple[str, str]]


def _parser(prog: str, description: str, commands: Commands) -> argparse.ArgumentParser:
    width = max(map(len, commands))
    listed = "\n".join(
        f"  {name:<{width}}  {line}" for name, (_, line) in commands.items()
    )
    parser = argparse.ArgumentParser(
        prog=prog,
        usage=f"{prog} [-h] command [argument ...]",
        description=description,
        epilog=f"commands:\n{listed}\n\nEach command's own -h says what it takes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "command",
        choices=commands,
        metavar="command",
        help="one of those below",
    )
    return parser


def dispatch(
    argv: Sequence[str], *, prog: str, description: str, commands: Commands
) -> int:
    parser = _parser(prog, description, commands)
    if not argv:
        parser.print_help()
        return 0
    # Only the name is read here: the rest is the command's.
    name = parser.parse_args(argv[:1]).command
    module = importlib.import_module(commands[name][0])
    return module.main(argv[1:], prog=f"{prog} {name}")
