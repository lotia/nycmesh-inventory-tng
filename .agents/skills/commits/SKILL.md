---
name: commits
description: Use when landing work in this repository - staging, writing or wording a commit message, splitting a change that spans two issues, or preparing a branch to push. Covers what one commit may hold and how its message is written.
---

# Committing

Everything about landing a commit — what one may hold, how its message is
written, staging by path, splitting work that bled across two issues, and
checking the message before it lands — is [Commits](../../../docs/commits.md).
It is written for people, and there is nothing in it an agent does
differently. Load that page when you are about to land something, not while
you are still working.

What is worth saying here, because an agent meets it and a person seldom does:
write the message to a file and hand it to `scripts/check-commit.sh`, so that
what is checked is what will land rather than the last message you composed.
