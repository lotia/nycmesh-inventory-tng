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
   tool. Run `bd prime` if beads context is missing. Label every bead you
   file: run `bd label list-all` first and reuse a label that fits before
   making a new one. Label a bead `needs-decision` whenever it waits on the
   person, and remove the label once they answer. A bug only requests at
   once can cause is labelled `race` and is P1, ahead of features.
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
   Then check and push as [Working on an issue](CONTRIBUTING.md#working-on-an-issue)
   says, every time.
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
   finds:** `mise run check-markdown`. Never disable, relax or work around
   a rule (config, inline comments, ignores) without asking the person
   first. The ignores in `.markdownlint-cli2.jsonc` leave out only files
   tools write.
10. **Development workflows are mise tasks**, in `mise.toml` or as scripts
    in `mise-tasks/`, each with a description, so `mise tasks` lists it
    with what it does. Compose runs through `mise run compose --`, never a
    runtime's own compose command.
    `backend/tests/codebase/test_contributing.py` enforces both.
11. **Never refer to an artifact** (a claude.ai artifact link, its id, or
    words like "the questions page") in beads, docs, decision records or
    code comments, unless the person asks. Artifacts are tools to guide the
    work, deleted when it is done. Write the decision itself, in words, with
    who decided and when, not as a choice's letter or a question's code.
    `backend/tests/codebase/test_artifact_links.py` refuses artifact links
    and such citations in the clone's files.
12. **Every file and module is clearly named and does one thing.** Its
    name says what it holds: a reader can guess the functions and classes
    inside from the name, and nothing inside surprises them. When a module
    grows a second job, split it into files named for each job rather than
    widening the name. This is a judgement, not a check. Where code goes,
    and how files and tests are named, is in
    `docs/decisions/0023-code-laid-out-by-feature.md`.
13. **Work goes in batches, each with a bead of its own.** The batch bead
    says the order of its issues and anything else the batch needs, and
    each issue blocks it (`bd dep add <batch> <issue>`).
    - **The next batch** is the one bead labelled `do-next`. A planned
      batch gets the label only once the person agrees it comes next.
      Only one bead ever carries it: move the label, never add a second.
    - **Starting a batch** takes the label off and marks the batch in
      progress (`bd label remove <batch> do-next`, then
      `bd update <batch> --claim`). Mark each issue in progress as its
      work starts (`bd update <issue> --claim`).
    - **After a restart**, `bd list --status in_progress` shows the batch
      under way and its issues being worked. With none,
      `bd list --label do-next` shows what comes next.
    - **A P1 bug found while working a batch:** one the batch's own
      changes cause is fixed in that batch, before it is pushed. One that
      was there already, or is unrelated, goes first in the `do-next`
      batch: add it to that bead, put it at the front of its order, and
      tell the person at the next check-in. With no `do-next` batch, it
      leads the next one planned. A P0 bug: stop and ask whether the
      current batch takes it before it is pushed.
    - **A batch of one** is still a batch: a P1 worked on its own gets a
      bead of its own, so the restart check, stages and check-in stay
      the same.
    - **Order between batches** is dependencies between their beads
      (`bd dep add <later> <earlier>`); `do-next` marks only the one to
      start next.
    - **Inserting a batch** ahead of the `do-next` one, once the person
      agrees it goes first: move the label to it, make the displaced
      batch wait on it (`bd dep add <displaced> <inserted>`), and note
      on the displaced batch what was put ahead of it, when, and who
      decided. Its plan stays as it was. When the inserted batch is
      pushed and closed, the label goes back to the displaced one
      without asking again. Several inserted in a row each wait on the
      one before.
    - **An urgent batch while one is in progress:** ask whether to
      finish and push the current batch first or pause it. A paused
      batch stays in progress and waits on the urgent one
      (`bd dep add <paused> <urgent>`), so `bd show` says why it waits.
    - **An investigation batch** has its own stages: investigate,
      review the findings, report to the person, file beads for what
      it found. Nothing is simplified or pushed; it closes once the
      person has the report and the beads are filed.
    - **The batch bead closes** once its issues are closed and pushed.

## More guidance

Agent-only guidance lives in [`.agents/`](.agents/). Load it only when the
task needs it.

| Working on | Read |
| --- | --- |
| beads commands in depth | `.agents/skills/beads/SKILL.md` |
| where code goes, and which way imports go | `docs/decisions/0023-code-laid-out-by-feature.md` |
