# Contributing

NYC Mesh is a volunteer community, and this project is built by volunteers. You
do not need to be a Django expert, a Kubernetes expert, or an AI-tooling expert
to help. If you can get the app running locally, you can contribute.

## Getting set up

Follow [DEVELOPERS.md](DEVELOPERS.md). It should take you from a clean machine to
a running application.

If it does not work, **that is a bug worth reporting.** Setup instructions that
have quietly drifted out of date are the most common reason a willing volunteer
gives up, so we treat them as seriously as broken code.

## Finding something to work on

Two trackers, and you may use either:

- **GitHub issues** — the front door. Anything labelled `good first issue` is
  scoped to be approachable without deep context.
- **beads** (`bd ready`) — a CLI tracker used for day-to-day work, especially by
  contributors working with AI agents. See
  [DEVELOPERS.md](DEVELOPERS.md#issue-tracking).

You are not required to use beads. Nothing in this project should be workable
*only* by an AI agent — if you hit something that seems to assume one, say so.

## Making a change

1. Fork the repository and create a branch.
2. Make your change, with a test for new behaviour.
3. Work through the [Definition of Done](DEVELOPERS.md#definition-of-done).
   The documentation item is not optional — if your change makes any instruction
   in this repository wrong, fix that instruction in the same pull request.
4. Open a pull request describing what changed and why.

Small pull requests get reviewed faster than large ones. A change that only
fixes a confusing sentence in the docs is a genuinely useful contribution.

## Documentation conventions

Read [Documentation rules](DEVELOPERS.md#documentation-rules) before writing
docs. The short version: **one topic lives in exactly one file**, and everything
else links to it. Please do not paste an explanation into a second place — link
to where it already lives.

## Working with AI coding agents

Agents are welcome here, and so is not using them. If you use one, point it at
[AGENTS.md](AGENTS.md); it is deliberately short and links out to deeper context
only when a task needs it.

Everything an agent is asked to do — tests, linting, documentation currency —
is exactly what a human contributor is asked to do. There is one standard.

## Questions

Open a GitHub issue, or ask in the NYC Mesh Slack. Asking early is cheaper for
everyone than guessing.
