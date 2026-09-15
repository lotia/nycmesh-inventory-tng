# Developer Guide

From a clean machine to a running application, in the order you will type it,
and then [a table of where everything else lives](#everything-else). If you
are new to the project, read [CONTRIBUTING.md](CONTRIBUTING.md) first — it is
how work gets picked up and reviewed. For what the project *is*, see
[README.md](README.md); for how it is put together,
[docs/architecture.md](docs/architecture.md).

**This guide is expected to work.** If a command here fails on a clean machine,
that is a bug in the guide — please open an issue or fix it in your next pull
request. CI runs the setup below on a clean machine every push, so that
expectation is checked rather than hoped for:
[What CI proves](docs/ci.md).

---

## Prerequisites

Two things, and nothing else installed globally:

| Tool | Why | Install |
| --- | --- | --- |
| [mise](https://mise.jdx.dev/getting-started.html) | Installs the pinned Python, Node, uv, and Helm versions. No system Python or Node needed. | `curl https://mise.run \| sh` |
| [Docker](https://docs.docker.com/get-started/get-docker/) or [Podman](https://podman.io/) | Runs PostgreSQL, and optionally the whole stack. Either one; see [below](#podman). | Platform installer |

If you intend to run a coding agent in this repository — Claude Code, Codex,
Gemini or another — there are three more, and they are listed separately
because they are **not** what running the application needs:

| Tool | Why an agent session needs it |
| --- | --- |
| `git` | Every guardrail in `scripts/` reads the repository through it. |
| `python3` | The commit checker and [the landing gate](docs/pull-requests.md#the-landing-gate) both read through it. |
| [`gh`](https://cli.github.com/), authenticated | The landing gate asks GitHub what a pull request's head is and what has been posted to it. `gh auth login` once. |

**A shim is not enough for these three, and that is the whole reason they are
called out.** Hooks and git hooks inherit whatever `PATH` the invoking process
had, which is not always an activated shell: a GUI git client, a launcher, or a
terminal where mise was never activated are all real cases. `mise.toml` pins a
Python for the project rather than installing one globally, so `python3` can be
absent exactly there. The checkers refuse rather than pass when one of these is
missing — that is deliberate, and
[the landing gate](docs/pull-requests.md#the-landing-gate) explains why — so the
symptom is a command that will not run and a message naming the program, not a
guard that quietly stopped guarding.

```bash
git --version && python3 --version && gh auth status
```

## Podman

[Podman](https://podman.io/) works too, and the two are interchangeable
everywhere in this project — substitute `podman compose` for `docker compose`
in the commands below and nothing else changes. The images are fully qualified
(`docker.io/library/...`) so Podman resolves them without extra configuration,
and every restraint `compose.yaml` places on a service was chosen to hold under
rootless Podman.

One thing is Podman's alone: `podman compose` does not implement compose
itself, it hands the work to an external provider that speaks the Docker API,
so Podman's API socket has to be listening or the command stops at `failed to
connect to the docker API`.

```bash
systemctl --user enable --now podman.socket
```

`enable --now` starts it and keeps it across logins; plain `start` lasts only
until you log out.

## Activate mise, then open a new shell

**Do this before typing anything else.** The installer above only puts a binary
in `~/.local/bin`; it changes no shell of yours. Its last lines print the one
line to add to your shell's configuration, of the form:

```bash
eval "$(~/.local/bin/mise activate bash)"      # or zsh, or fish
```

Add the line it printed — to `~/.bashrc`, `~/.zshrc`, or
`~/.config/fish/config.fish` — and then **open a new shell**, because a running
one will not pick it up. Everything after this point in this guide, and in the
pages it links to, assumes an activated shell.

Until it is activated, `mise` may not be found at all, and `uv`, `python`,
`node`, `npm` and `helm` certainly are not: this project installs none of them
globally, so a shell that cannot see mise's shims answers `command not found`
to every command below. The new shell should answer both of these:

```bash
mise --version
mise doctor       # "activated: yes" is the line that matters
```

Every version this project uses is pinned in [`mise.toml`](mise.toml), which
is the single source of truth for the toolchain — CI installs from it too.
Three of the names it brings recur throughout, so they are worth having
straight now: **uv** manages the backend's Python environment, and `uv run
<command>` is how every backend command is run inside it; **ty** is the Python
type checker; **helm** renders the deployment chart.

## Clone and bootstrap

```bash
git clone https://github.com/lotia/nycmesh-inventory-tng.git
cd nycmesh-inventory-tng
scripts/bootstrap-dev.sh
```

That URL is public and read-only, and needs no GitHub account and no SSH key.
Where a remote you can push to matters is [Pull requests](docs/pull-requests.md).
`mise run setup` is the same script under the name mise lists it by, so
`mise tasks` in a fresh clone answers "what am I supposed to run?" without your
having found this page first.

[`scripts/bootstrap-dev.sh`](scripts/bootstrap-dev.sh) is setup: it trusts and
installs the toolchain, writes `.env` from [`.env.sample`](.env.sample) if you
have none, points git at the hooks that check a commit as you make one, starts
PostgreSQL, applies the migrations, and puts an invented catalogue in the
database so that no screen you open is blank. It composes commands you could
type yourself — [Local development](docs/local-development.md#what-bootstrap-does-by-hand)
lists them — and invents nothing of its own. Run it as often as you like; it
writes no file it has written already, and it will not touch a `.env` you have
edited. If it stops on a port that is already taken, or on anything else,
[Troubleshooting](docs/local-development.md#troubleshooting) names the fix.

It stops short of one thing, and says so as it finishes: `createsuperuser`
asks for a password at a terminal, so it cannot run unattended. The last thing
it prints is what to do next — the two label codes the seed made, and the
order to start the servers in. Read the end of its output rather than
scrolling past it: those codes are the stickers a scanner resolves, and
nothing else prints them.

---

## Running it

Three ways, and this is the first: everything in Docker, which is the
quickstart, so the commands live in [README](README.md#quickstart) rather than
a second time here. Frontend on <http://localhost:8080>, API on
<http://localhost:8000>, and migrations run automatically on start. The other
two — the native servers the bootstrap script has just prepared, which reload
as you edit, and a devcontainer — are
[Local development](docs/local-development.md#running-it-one-way-at-a-time),
which also says why you run one way at a time.

**It seeds itself, so no screen you open is blank**: a one-shot `seed`
service runs `seed_demo_data` once migrations have finished, and is
idempotent. **What it does not give you is a login**, for the reason above,
and [README](README.md#quickstart) hands you that step.

Three more worth knowing once it is up:

```bash
docker compose exec backend python manage.py show_label_codes  # what to scan
docker compose logs -f backend | scripts/pretty-logs           # tail logs
docker compose down -v                                         # stop, wipe database
```

The first is there because the seed prints its label codes only once, and
they are what you type into the box marked **Scan or type a code**. What the
stack does when seeding is turned off, and the restraints every service in it
runs under, are [The Docker stack](docs/local-development.md#the-docker-stack).

**The camera works from your phone, and it is behind a profile**, because
plain HTTP is not enough for it anywhere but your own machine —
[decision 0011](docs/decisions/0011-qr-batch-scanning.md#consequences) is why.
[Using the camera from a phone](docs/local-development.md#using-the-camera-from-a-phone)
is the one command. **If something did not start**,
[Troubleshooting](docs/local-development.md#troubleshooting) names the fix; a
stack from before August 2026 needs
[one command first](docs/local-development.md#if-you-had-this-stack-running-before-august-2026).

## Signing in

Everybody signs in, for now, so neither half of the app answers until you
have an account: make one, then sign in at `/accounts/login/` on whichever
address you are using. Locally a password is the whole of it — this repository
turned the second factor off, and the code's own default is the opposite.
Which endpoints answer without a session, how to turn the second factor on
when you are working on the sign-in flow itself, and the login that survives
a database wipe are [Signing in](docs/local-development.md#signing-in).

---

## Everything else

This guide stops at a running application. Every other topic has one page,
and this table is where each lives; [CONTRIBUTING.md](CONTRIBUTING.md) is the
rule that keeps it to one, and the checks that hold the table to the
`docs/` directory in both directions.

| Topic | Where |
| --- | --- |
| What the project is, and the quickstart | [README.md](README.md) |
| Using the app to move stock | [guides/volunteer.md](guides/volunteer.md) |
| Running the catalogue, the people and the labels | [guides/administrator.md](guides/administrator.md) |
| How to contribute: the Definition of Done, and the documentation rules | [CONTRIBUTING.md](CONTRIBUTING.md) |
| The other ways to run it, the camera from a phone, signing in, troubleshooting | [docs/local-development.md](docs/local-development.md) |
| Repository layout, common tasks, the database, the logs, the API schema | [docs/working-in-the-code.md](docs/working-in-the-code.md) |
| Code style and typing — `uv run ruff check --fix . && uv run ruff format .`, `npm run lint:fix` | [docs/code-style.md](docs/code-style.md) |
| Testing and coverage, and how the guides' pictures are made | [docs/testing.md](docs/testing.md) |
| Which documents CI executes, and how far | [docs/ci.md](docs/ci.md) |
| Issue tracking: bd, where its database lives, and the GitHub mirror | [docs/issue-tracking.md](docs/issue-tracking.md) |
| Triaging an issue somebody else filed | [docs/triage.md](docs/triage.md) |
| What one commit contains, its message, and how to land it | [docs/commits.md](docs/commits.md) |
| The git hooks: what runs, what each refuses, how they are installed | [docs/git-hooks.md](docs/git-hooks.md) |
| How work is reviewed, reaches `main`, and is merged | [docs/pull-requests.md](docs/pull-requests.md) |
| Architecture and technology choices | [docs/architecture.md](docs/architecture.md) |
| Inventory data model | [docs/data-model.md](docs/data-model.md) |
| Deployment | [docs/deployment.md](docs/deployment.md) |
| Telemetry: where it can be sent, and what it may carry | [docs/observability.md](docs/observability.md) |
| Why a decision was made | [docs/decisions/](docs/decisions/) |
| The investigation behind a decision | [docs/briefs/](docs/briefs/) |
| Rules for AI coding agents | [AGENTS.md](AGENTS.md) |
| Configuration variables | [.env.sample](.env.sample) |
| Toolchain versions | [mise.toml](mise.toml) |
