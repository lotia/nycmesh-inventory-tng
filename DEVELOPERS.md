# Developer Guide

Everything you need to set up a development environment and work on
inventory-tng. If you are new to the project, read
[CONTRIBUTING.md](CONTRIBUTING.md) first — it explains how work gets picked up
and reviewed. For what the project *is*, see [README.md](README.md); for how it
is put together, see [docs/architecture.md](docs/architecture.md).

**This guide is expected to work.** If a command here fails on a clean machine,
that is a bug in the guide — please open an issue or fix it in your next pull
request. See [Definition of Done](#definition-of-done). CI runs the setup below
on a clean machine every push, so that expectation is checked rather than
hoped for: [What CI proves](docs/ci.md#what-ci-proves).

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
| `python3` | The commit checker and [the landing gate](docs/pull-requests.md#when-a-branch-is-ready-to-merge) both read through it. |
| [`gh`](https://cli.github.com/), authenticated | The landing gate asks GitHub what a pull request's head is and what has been posted to it. `gh auth login` once. |

**A shim is not enough for these three, and that is the whole reason they are
called out.** Hooks and git hooks inherit whatever `PATH` the invoking process
had, which is not always an activated shell: a GUI git client, a launcher, or a
terminal where mise was never activated are all real cases. `mise.toml` pins a
Python for the project rather than installing one globally, so `python3` can be
absent exactly there. The checkers refuse rather than pass when one of these is
missing — that is deliberate, and
[the landing gate](docs/pull-requests.md#when-a-branch-is-ready-to-merge) explains why — so the
symptom is a command that will not run and a message naming the program, not a
guard that quietly stopped guarding.

```bash
git --version && python3 --version && gh auth status
```

### Podman

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

### Pinned versions

Every version this project uses is pinned in [`mise.toml`](mise.toml). That file
is the single source of truth for the toolchain — CI installs from it too.

### Activate mise, then open a new shell

**Do this before typing anything else.** The installer above only puts a binary
in `~/.local/bin`; it changes no shell of yours. Its last lines print the one
line to add to your shell's configuration, of the form:

```bash
eval "$(~/.local/bin/mise activate bash)"      # or zsh, or fish
```

Add the line it printed — to `~/.bashrc`, `~/.zshrc`, or
`~/.config/fish/config.fish` — and then **open a new shell**, because a running
one will not pick it up. Everything after this point in this guide, and in the
two documents it links to, assumes an activated shell.

Until it is activated, `mise` may not be found at all, and `uv`, `python`,
`node`, `npm` and `helm` certainly are not: this project installs none of them
globally, so a shell that cannot see mise's shims answers `command not found`
to every command below. The new shell should answer both of these:

```bash
mise --version
mise doctor       # "activated: yes" is the line that matters
```

Three of the names mise brings recur throughout, so they are worth having
straight now: **uv** manages the backend's Python environment, and `uv run
<command>` is how every backend command is run inside it; **ty** is the Python
type checker; **helm** renders the deployment chart.

### Clone and bootstrap

```bash
git clone https://github.com/lotia/nycmesh-inventory-tng.git
cd nycmesh-inventory-tng
scripts/bootstrap-dev.sh
```

That URL is public and read-only, and needs no GitHub account and no SSH key.
Where a remote you can push to matters is [Pull requests](docs/pull-requests.md#pull-requests).

`mise run setup` is the same script under the name mise lists it by, so
`mise tasks` in a fresh clone answers "what am I supposed to run?" without your
having found this page first.

[`scripts/bootstrap-dev.sh`](scripts/bootstrap-dev.sh) is setup: it trusts and
installs the toolchain, writes `.env` from [`.env.sample`](.env.sample) if you
have none, points git at the hooks that check a commit as you make one, starts
PostgreSQL, applies the migrations, and puts an invented catalogue in the
database so that no screen you open is blank. It composes the
commands the rest of this guide describes and invents nothing of its own, so
nothing here is out of reach if you would rather type them. Run it as often as
you like; it writes no file it has written already, and it will not touch a
`.env` you have edited.

If it stops on a port that is already taken, or on anything else,
[Troubleshooting](docs/local-development.md#troubleshooting) is two sections down and names the fix for
each of them.

It stops short of two things, and says both as it finishes rather than leaving
you to find them here. `createsuperuser` asks for a password at a terminal, so
it cannot run unattended. And the first sign-in of the account it makes needs
[a second factor](docs/local-development.md#signing-in), which means having an authenticator app to
hand.

The last thing it prints is what to do with it: the two label codes the seed
made, and the order to start the servers in. Read the end of its output rather
than scrolling past it — those codes are the stickers a scanner resolves, and
nothing else prints them.

`.env` holds your local configuration. It is git-ignored, and
[`.env.sample`](.env.sample) documents every variable. Setting the toolchain
and that file up by hand, if you would rather, is three commands:

```bash
mise trust      # allow mise to use this repo's mise.toml
mise install    # installs Python, Node, uv, Helm at the pinned versions
cp .env.sample .env
```

`mise trust` is a one-off confirmation that you meant to run the versions this
repository asks for: mise refuses to read a `mise.toml` it has not been told
about, so that a checkout cannot pick your toolchain for you unnoticed.

---

## Running it

Three ways. Everything in Docker is below, and is the quickstart. The other
two — the native servers, which the bootstrap script above has just prepared
and which reload as you edit, and a devcontainer, with nothing installed on
your host — are in [Local development](docs/local-development.md#running-it-one-way-at-a-time),
which also says why you run one way at a time.

**The camera works from your phone, and it is behind a profile.** Every way of
running it serves plain HTTP, which is not enough for the camera anywhere but
your own machine —
[decision 0011](docs/decisions/0011-qr-batch-scanning.md#consequences) has the
rule and why the refusal used to read as a bug in the app. Turning that into a
working camera is one command:
[Using the camera from a phone](docs/local-development.md#using-the-camera-from-a-phone).

### Option A — everything in Docker

Best for a first run, or when you only care about one half of the stack. It is
the quickstart, so the commands live in
[README](README.md#quickstart) rather than a second time here. Frontend on
<http://localhost:8080>, API on <http://localhost:8000>, and migrations run
automatically on start.

**It seeds itself, so no screen you open is blank.** A one-shot `seed` service
runs [`seed_demo_data`](docs/working-in-the-code.md#common-tasks) once the backend is answering, which is
also once migrations have finished. It is idempotent, so bringing the stack up
again changes nothing, and it will not invent stock on top of a ledger holding
anything it did not write — [decision 0016](docs/decisions/0016-invariants-for-every-writer.md)
makes those rows unremovable, so that guard is what makes seeding unattended
safe.

**What it does not give you is a login.** `createsuperuser` asks for a password
at a terminal, so Option A cannot make one for you and
[README](README.md#quickstart) hands it to you as a step. That is the one thing
this option leaves you to do by hand.

Three more worth knowing once it is up:

```bash
docker compose exec backend python manage.py show_label_codes  # what to scan
docker compose logs -f backend | scripts/pretty-logs           # tail logs
docker compose down -v                                         # stop, wipe database
```

The first is there because the seed prints its label codes once, in
`docker compose logs seed`, and those codes are what you type into the box
marked **Scan or type a code**. Nothing else in the application shows them, so
ask for them whenever you need them rather than scrolling for them.

`DJANGO_DEBUG=false` in your `.env` turns the seeding off, because the command
refuses to run without it. The stack still comes up and still serves; it comes
up empty, and `docker compose ps` shows the `seed` service exited non-zero with
the refusal in its log. That is the refusal working, not the stack failing.

Every service in [`compose.yaml`](compose.yaml) names the non-root uid it runs
as, drops all capabilities, refuses to gain privileges, and runs with a
read-only root filesystem — with a `tmpfs` for each directory that service
genuinely writes to, and no others. Those are stated in the file rather than
claimed in a comment, and none of them needs anything added under rootless
Podman.

**If something did not start**, the port, the missing `.env` and the shell
that cannot find `uv` are each a line in
[Troubleshooting](docs/local-development.md#troubleshooting). A stack you
had running before August 2026 needs
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

## Documentation rules

Two rules govern documentation in this repository. They exist because NYC Mesh
is a volunteer community: stale or scattered setup docs are the single biggest
thing standing between a willing volunteer and a first contribution.

### 1. One topic, one place

Every piece of documentation lives in exactly **one** file. Everything else
links to it with a relative Markdown link.

If you find yourself explaining something that is already explained elsewhere,
delete your copy and link instead. Two copies of an instruction means one of
them is wrong within a month, and the reader has no way to tell which.

Where each topic lives:

| Topic | Canonical location |
| --- | --- |
| What the project is, quickstart | [README.md](README.md) |
| Using the app to move stock | [guides/volunteer.md](guides/volunteer.md) |
| Running the catalogue, the people and the labels | [guides/administrator.md](guides/administrator.md) |
| Testing and coverage, and how the guides' pictures are made | [docs/testing.md](docs/testing.md) |
| Which documents CI executes, and how far | [docs/ci.md](docs/ci.md) |
| Development setup and workflow | This file |
| The other ways to run it, the camera from a phone, signing in, troubleshooting | [docs/local-development.md](docs/local-development.md) |
| Repository layout, common tasks, the database, the logs, the API schema | [docs/working-in-the-code.md](docs/working-in-the-code.md) |
| Code style and typing — `uv run ruff check --fix . && uv run ruff format .`, `npm run lint:fix` | [docs/code-style.md](docs/code-style.md) |
| API schema and how it stays current | [The API schema](docs/working-in-the-code.md#the-api-schema) |
| Reading logs while developing | [Reading the logs while you work](docs/working-in-the-code.md#reading-the-logs-while-you-work) |
| Issue tracking: bd, where its database lives, and the GitHub mirror | [docs/issue-tracking.md](docs/issue-tracking.md) |
| What one commit contains, its message, and how to land it | [docs/commits.md](docs/commits.md) |
| The git hooks: what runs, what each refuses, how they are installed | [docs/git-hooks.md](docs/git-hooks.md) |
| How work is reviewed and reaches `main`, and merged | [docs/pull-requests.md](docs/pull-requests.md) |
| How to contribute | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Architecture and technology choices | [docs/architecture.md](docs/architecture.md) |
| Inventory data model | [docs/data-model.md](docs/data-model.md) |
| Deployment | [docs/deployment.md](docs/deployment.md) |
| Telemetry: where it can be sent, and what it may carry | [docs/observability.md](docs/observability.md) |
| Why a decision was made | [docs/decisions/](docs/decisions/) |
| The investigation behind a decision | [docs/briefs/](docs/briefs/) |
| Rules for AI coding agents | [AGENTS.md](AGENTS.md) |
| Configuration variables | [.env.sample](.env.sample) |
| Toolchain versions | [mise.toml](mise.toml) |

Four checks in CI keep that arrangement from rotting, and each can be run by
hand:

```bash
scripts/check-docs.sh          # the same passage in two files
scripts/check-docs.sh --words 8   # stricter, if you are hunting one down
scripts/check-docs.sh --budget    # this file and CONTRIBUTING.md, within their budgets
scripts/check-anchors.sh       # a heading named outside Markdown that is not there
scripts/check-config.sh        # a configuration value nobody explained
```

One topic, one place says nothing about how long the place may be, and this
guide grew to two thousand lines under it. `--budget` is the other half: the
page a newcomer is sent to first stays one they can finish, and
[`scripts/check-docs.budget`](scripts/check-docs.budget) is where each number
is set and argued.

A link checker catches a link whose target you renamed, fragment included, in
every Markdown file. `check-anchors.sh` asks the same of every `<page>.md#<anchor>`
written anywhere else — a docstring, a shell comment, a refusal message, a
workflow — which a link checker never reads and a reader meets at the moment
something has just refused them. `check-docs.sh` catches
the other half — an explanation pasted into a second file rather than linked to
— by comparing prose in runs of twelve words. Code blocks, tables, headings and
link text are not prose and are left out, so a repeated command or a repeated
citation is not reported.

It reads every Markdown file **and the comments of everything else** — scripts,
workflows, the chart's templates, and the application's own docstrings and
comments. Those are documentation of how this repository works, and a docstring
is the easiest place of all to re-derive a decision record.

`check-config.sh` holds the row above it — that configuration variables are
documented where they are declared. Every value in
[`.env.sample`](.env.sample), [`compose.yaml`](compose.yaml) and the chart's
`values.yaml` must have prose beside it, and every variable the chart renders
into a container must appear in
[deployment](docs/deployment.md#environment-variables). That last rule is the
one worth having: it is what found a rate limit an operator could set, the
chart would honour, and the document listing what may be set had never heard
of.

What counts as documented is what a reader uses rather than what is easy to
check — a comment above the *group* a value belongs to, and in YAML a comment
on an enclosing key covering what is nested beneath it. Grouping related values
under one comment is the better way to write these files, so a checker that
demanded one comment per line would be arguing against the house style. What it
cannot judge is whether the prose is any good; `scripts/check-config.allow` is
for the few values that genuinely need none, and an entry there wants a reason
like every other allowlist here.

The corpus is every file in your checkout that git will admit to — committed or
not, so long as `.gitignore` does not cover it — less the ones whose prose
nobody here writes:
images, fonts, a compiled module, a spreadsheet, the two lock files a resolver
produces, and `.beads/` entire — the tracker's exports are data, its five git
hooks are generated and repeat a banner between themselves, and its `README.md`
arrived with the tool. That last one is the only thing kept out for *where* it
sits, and it is the only directory anybody here does not author; everywhere
else a file added or moved under a directory already read is read by default,
whatever it is called. The rule is stated as a subtraction on purpose.
It was once a list of seven extensions, and the files whose whole job is
explaining something — `.env.sample`, the chart's `_helpers.tpl`, the
Dockerfiles, `nginx.conf.template`, the extensionless programs under `scripts/`
— were precisely the ones it left out. What is left out *within* a file is not
prose: fenced blocks and
tables in Markdown, and in code, anything a file *uses* rather than *says* — a
string handed to `RunSQL` is a value however much of it reads like a sentence.
Addressing is left out too, wherever it appears: a Markdown link, a bare path
or URL in a comment, and a bare "decision 0016" all name a thing rather than
explain it, and two files naming the same thing are obeying the rule.

The judgement this leaves is real and is per passage. A docstring beside the
invariant it enforces is the code explaining itself, which is a different thing
from a topic having two homes; when it is the first, the fix is usually still
to state the rule here and cite the record rather than reproduce its argument.

When it objects, the fix is almost always to delete one copy and link to the
other. `scripts/check-docs.allow` exists for the rare passage that is genuinely
meant to appear twice; its own header says how an entry is written and when one
is warranted. An allowance covers a named pair of files, so a third copy is
still reported, and one that stops matching anything is reported too — a
baseline cannot outlive the repetition it recorded.

### 2. Docs change with the code that invalidates them

Documentation is updated in the **same** change as the code, not afterwards.
This is part of [Definition of Done](#definition-of-done) below.

---

## Definition of Done

A change is not finished — and an issue must not be closed — until all of these
hold:

- [ ] Tests and coverage thresholds pass (`uv run pytest`, `npm test`) —
      see [Testing and coverage](docs/testing.md#testing-and-coverage)
- [ ] Lint, format, and type checks pass — see [Code style](docs/code-style.md#code-style)
- [ ] Every function you added or changed is annotated — see [Typing](docs/code-style.md#typing)
- [ ] New behaviour has a test. If you added code that coverage counts, it is
      covered; if you excluded something, the exclusion is justified in the
      pull request
- [ ] **Code that changes something says so.** Run
      `scripts/check-telemetry.sh`: its header states what it reads and what it
      leaves alone, and `scripts/check-telemetry.allow` is where a module that
      is right to stay quiet is argued. What is worth recording, and what may
      never be recorded at all, is
      [docs/observability.md](docs/observability.md)
- [ ] **Documentation is consistent with the change.** If the change alters
      setup steps, commands, environment variables, architecture, the API
      surface, or the deployment procedure, the canonical document for that
      topic is updated in the same pull request.
- [ ] **The two guides still describe this app.** Weigh the change against
      [guides/volunteer.md](guides/volunteer.md) and
      [guides/administrator.md](guides/administrator.md): neither may name a
      role the app has dropped, nor omit one of its flows. This is the part no
      checker sees — [What CI proves](docs/ci.md#what-ci-proves) is the part that is seen
- [ ] A decision that future readers would ask "why?" about has a record in
      [docs/decisions/](docs/decisions/)
- [ ] **No cryptography was written.** An established library, or a thin
      wrapper over one's public API, or the work stopped and asked — see
      [rule 3 in AGENTS.md](AGENTS.md#three-rules-that-are-not-negotiable)

The documentation item is not a formality and not a follow-up ticket. A change
that leaves the docs describing the old behaviour is incomplete, because the
next person to read them will be misled.

---
