# Contributing

## Version control and issues

Issues are tracked with [beads](https://github.com/gastownhall/beads) (`bd`).
For version control, use git or [jj](https://jj-vcs.github.io/jj/), whichever
you prefer. Coding agents always use jj.

### Setup, once per clone

```sh
git clone <url>           # or: jj git clone <url>
bd bootstrap              # fetch the issue database; never `bd init`
```

To use jj in a git clone, run `jj git init --colocate` in it.

Issues live in a database under `.beads/`, not in commits, and travel through
the same remote on a ref of their own. `bd sync` pulls and pushes them. Run it
before you start and after you change an issue.

### Working on an issue

```sh
bd sync
bd ready                  # pick something unblocked
bd update <id> --claim
# make one commit (or jj change) for the issue,
# with a message that starts with "<id>: "
bd close <id>
bd sync
```

Then push a branch and open a pull request: `git push -u origin <branch>`, or
`jj git push --change @` with jj.

## Running the checks

You need [uv](https://docs.astral.sh/uv/) and a container runtime with compose,
such as Docker or Podman. uv installs the Python version the backend asks for.

```sh
docker compose up -d --wait db   # or: podman compose up -d --wait db
cd backend

# Optional: let the tools fix what they can. Neither changes behaviour.
uv run ruff check --fix
uv run ruff format

# Then check, in this order.
uv run ty check                  # types
uv run ruff check                # lint
uv run ruff format --check       # formatting
uv run pytest                    # tests, against the database started above
```

Only the tests need the database; the other three take about a second. With
the virtualenv activated (`source backend/.venv/bin/activate`), drop the
`uv run`.
If you use [mise](https://mise.jdx.dev/), `mise run fix` runs the two fixes
and `mise run check` runs the four checks; start the database first.

The database listens on port 5433, so it can run beside MeshDB's. If that port
is taken, put `INVENTORY_DB_PORT=5434` (or any free port) in a `.env` file at
the top of the repository; compose and the tests both read it. Each test run
creates a throwaway database and drops it at the end. Write tests in pytest
style.
