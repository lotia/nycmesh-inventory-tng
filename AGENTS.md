# AGENTS.md

Instructions for AI coding agents, whatever the harness. For what the project
is, read [README.md](README.md).

## Rules

1. **Use `jj`, never `git`.** That includes read-only commands: use `jj st`,
   `jj diff` and `jj log`. `git` is denied in this repository's Claude Code,
   Codex and Cursor settings. People may use either, so if the clone has no
   `.jj` directory, ask before running `jj git init --colocate`. How to work
   with jj and beads here is in
   [CONTRIBUTING.md](CONTRIBUTING.md#version-control-and-issues). It is written
   for people and agents alike, so follow it as written.
2. **Track work in beads (`bd`)**, not in TODO lists or a harness's own task
   tool. Run `bd prime` if beads context is missing.
3. **Never write cryptography.** Use an established library, or a thin wrapper
   that only arranges its public calls. If neither reaches what is needed,
   stop and say so. Do not prototype it.
4. **Run the checks before closing an issue that changed code.** See
   [Running the checks](CONTRIBUTING.md#running-the-checks).
5. **Review, then simplify, before pushing code.** First a review for bugs,
   then fix what it finds. Then a simplification pass, then fix again. Push
   only after both. In Claude Code, run `/code-review` and then `/simplify`
   yourself. In another harness, use its equivalents, or ask the person.
6. **Code you write logs what it does**, at the right level, and never logs
   secrets or personal data. See [Logging](CONTRIBUTING.md#logging).
7. **Code you write is measured where it matters.** When you add something a
   volunteer does (a save, a move, a scan), add an `inventory.*` counter or
   histogram, named and given units per OpenTelemetry's conventions, with a
   test that reads it. Metric attributes take a few fixed values only: never
   names, ids, addresses or free text. Don't add spans (0006).
8. **Grow documentation deliberately.** Change an existing doc rather than
   adding one, and add a new file only when an issue asks for it.

## More guidance

Agent-only guidance lives in [`.agents/`](.agents/). Load it only when the
task needs it.

| Working on | Read |
| --- | --- |
| beads commands in depth | `.agents/skills/beads/SKILL.md` |
