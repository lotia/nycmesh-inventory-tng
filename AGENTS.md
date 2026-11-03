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
3. **Never implement cryptography.** No hashing, encryption, signing, key
   derivation, MAC or random-number algorithm written by hand, however
   small, and none copied in. Always call an established library or a
   platform API (Web Crypto, `hashlib`, `secrets`, `cryptography`), or a thin
   wrapper that only arranges its public calls. If none does what is needed,
   stop and say so. Do not prototype it.
4. **Run the checks before closing an issue that changed code.** See
   [Running the checks](CONTRIBUTING.md#running-the-checks).
5. **Review, then simplify, before pushing code.** First a review for bugs,
   then fix what it finds. Then a simplification pass, then fix again. Push
   only after both. In Claude Code, run `/code-review` and then `/simplify`
   yourself. In another harness, use its equivalents, or ask the person.
6. **Code you write logs what it does**, at the right level. Log ids, not
   names, emails, free text, request bodies or database error messages; never
   secrets. The table in [Logging](CONTRIBUTING.md#logging) is the rule.
7. **Code you write is measured where it matters.** When you add something a
   volunteer does (a save, a move, a scan), add an `inventory.*` counter or
   histogram, named and given units per OpenTelemetry's conventions, with a
   test that reads it. Metric attributes take values from a fixed list in
   code: never names, ids, addresses or free text. Don't add spans (0006).
8. **Keep documentation short, clear and current.** Follow
   [Documentation](CONTRIBUTING.md#documentation). In short: docs for people
   describe only what the current code needs, history goes in a decision
   record, and a change that alters what a doc says updates that doc in the
   same change, without being asked. Change an existing doc rather than
   adding one, and add a new file only when an issue asks for it.
9. **Lint every Markdown file you write or change, and fix everything it
   finds.** The linter is markdownlint; inventory-tng-pkm sets it up and
   cleans up the existing files. Until it lands, run
   `npx markdownlint-cli2@0.24.0 <files>` from the repository's root, where
   `.markdownlint-cli2.jsonc` is. Never disable, relax or work around a rule
   (config, inline comments, ignores) without asking the person first.
10. **Development workflows are mise tasks**, in `mise.toml` or as scripts
    in `mise-tasks/`, each named in CONTRIBUTING.md as `mise run <task>`.
    Compose runs through `mise run compose --`, never a runtime's own compose
    command. `backend/tests/test_contributing.py` enforces both.

## More guidance

Agent-only guidance lives in [`.agents/`](.agents/). Load it only when the
task needs it.

| Working on | Read |
| --- | --- |
| beads commands in depth | `.agents/skills/beads/SKILL.md` |
