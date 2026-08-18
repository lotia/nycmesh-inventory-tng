# 0003 — Django 6.1 rather than 5.2 LTS

**Status:** accepted

## Context

At the time of writing, Django 6.1 is the current release and Django 5.2 is the
current long-term support release, supported until April 2028. MeshDB runs an
LTS (4.2) for exactly the stability reason an LTS exists.

A regular Django release has roughly a sixteen-month support window; an LTS has
three years. For a volunteer project with intermittent maintenance, that
difference is not trivial.

## Decision

Use Django 6.1, the current stable release.

## Consequences

- Current features and current documentation, which matters when onboarding
  contributors who will search for answers online.
- Security support ends sooner than an LTS. The next LTS is expected to be
  Django 6.2 in April 2027, and the upgrade is tracked as an issue rather than
  left to be discovered at end of life.
- The project is greenfield, so there is no migration cost to being on a newer
  release now.
