# Contributing

NYC Mesh is a volunteer community, and this project is built by volunteers. You
do not need to be a Django expert, a Kubernetes expert, or an AI-tooling expert
to help. If you can get the app running locally, you can contribute.

## Getting set up

Follow [DEVELOPERS.md](DEVELOPERS.md): from a clean machine to a running
application, ending in a table of where every other topic lives.

If it does not work, **that is a bug worth reporting.** Setup instructions
that have quietly drifted are the commonest reason a willing volunteer gives
up, so we treat them as seriously as broken code.

## Finding something to work on

Two trackers, and you may use either:

- **GitHub issues** — the front door. Anything labelled `good first issue` is
  scoped to be approachable without deep context.
- **beads** (`bd ready`) — a CLI tracker used for day-to-day work, especially by
  contributors working with AI agents. See
  [Issue tracking](docs/issue-tracking.md).

You are not required to use beads. Nothing in this project should be workable
*only* by an AI agent — if you hit something that seems to assume one, say so.

## Making a change

1. Fork the repository and create a branch.
2. Make your change, with a test for new behaviour.
3. Work through the [Definition of Done](#definition-of-done) below.
4. Land it as one issue's worth of work, with a message written the way
   [Commits](docs/commits.md) describes.
5. Open a pull request describing what changed and why.

Review happens on the pull request, and everything reaches `main` through one.
Read [Pull requests](docs/pull-requests.md) before you start — it changes how
you will want to arrange your commits. Small pull requests get reviewed faster
than large ones, and one that only fixes a confusing sentence is a genuinely
useful contribution.

## Definition of Done

A change is not finished — and an issue must not be closed — until all of these
hold:

- [ ] Tests and coverage thresholds pass (`uv run pytest`, `npm test`) —
      see [Testing and coverage](docs/testing.md)
- [ ] Lint, format, and type checks pass — see [Code style](docs/code-style.md)
- [ ] Every function you added or changed is annotated — see
      [Typing](docs/code-style.md#typing)
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
      checker sees — [What CI proves](docs/ci.md) is the part that is seen
- [ ] A decision that future readers would ask "why?" about has a record in
      [docs/decisions/](docs/decisions/)
- [ ] **No cryptography was written.** An established library, or a thin
      wrapper over one's public API, or the work stopped and asked — see
      [rule 3 in AGENTS.md](AGENTS.md#three-rules-that-are-not-negotiable)

The documentation item is not a formality and not a follow-up ticket: a change
that leaves the docs describing the old behaviour is incomplete.

## Documentation rules

Two rules, and they exist because NYC Mesh is a volunteer community: stale or
scattered setup docs are the biggest thing between a volunteer and a first
contribution.

### One topic, one place

Every piece of documentation lives in exactly **one** file, and everything
else links to it. Where each topic lives is the table at the end of
[DEVELOPERS.md](DEVELOPERS.md#everything-else); every page under `docs/` has a
row in it.

If you find yourself explaining something that is already explained elsewhere,
delete your copy and link instead. Two copies of an instruction means one of
them is wrong within a month, and the reader has no way to tell which.

Four checks in CI keep that arrangement from rotting, and each can be run by
hand:

```bash
scripts/check-docs.sh          # the same passage in two files
scripts/check-docs.sh --words 8   # stricter, if you are hunting one down
scripts/check-docs.sh --budget    # the guide and this file, within their budgets
scripts/check-anchors.sh       # a heading named outside Markdown that is not there
scripts/check-config.sh        # a configuration value nobody explained
```

A link checker catches a link whose target you renamed, fragment included, in
every Markdown file; `check-anchors.sh` asks the same of every
`<page>.md#<anchor>` and `<page>.md "Heading"` written anywhere else, which a
reader meets at the moment something has just refused them. `check-docs.sh`
catches the other half — an explanation pasted into a second file rather than
linked to — by comparing prose in runs of twelve words, in every Markdown file
**and the comments of everything else**, because a docstring is the easiest
place of all to re-derive a decision record. Its header says what is read and
what is left out. When it objects, delete one copy and link to the other;
`scripts/check-docs.allow` is for the rare passage genuinely meant to appear
twice, and its own header says when that is.

`check-config.sh` holds that configuration is documented where it is declared:
every value in `.env.sample`, `compose.yaml` and the chart's `values.yaml` has
prose beside it, every variable the chart renders into a container is in
[deployment](docs/deployment.md#environment-variables), and every value
`compose.yaml` sets is one `.env` can reach, naming a variable `.env.sample`
declares. `--budget` holds the
guide and this file to a size a newcomer can finish;
[`scripts/check-docs.budget`](scripts/check-docs.budget) sets each number and
says why.

### Docs change with the code that invalidates them

Documentation is updated in the **same** change as the code, not afterwards.
This is part of the [Definition of Done](#definition-of-done) above.

## Working with AI coding agents

Agents are welcome here, and so is not using them. If you use one, point it at
[AGENTS.md](AGENTS.md); it is short, and links out only when a task needs it.

Everything an agent is asked to do is exactly what a human contributor is
asked to do. There is one standard, and it includes who may merge —
[When a branch is ready to merge](docs/pull-requests.md#when-a-branch-is-ready-to-merge).

## Questions

Open a GitHub issue, or ask in the NYC Mesh Slack. Asking early is cheaper for
everyone than guessing.
