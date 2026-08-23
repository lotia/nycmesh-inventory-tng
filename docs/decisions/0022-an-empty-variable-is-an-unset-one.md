# 0022 — An empty environment variable means the same as an absent one

**Status:** accepted

## Context

`django-environ` applies a declared default only when a variable is **absent**.
A variable set to the empty string is present, so `env("NUM_PROXIES")` hands
back `""`, `int("")` raises during settings import, and the process dies —
taking `check`, `migrate`, `collectstatic`, `runserver` and the whole test run
with it. The traceback names `int`, not the variable and not the file.

Both ways to arrive there are ordinary rather than exotic:

- A developer clears a value in `.env` rather than deleting the line.
  [`.env.sample`](../../.env.sample) all but teaches it, shipping several
  variables empty because empty is their real default.
- A values file blanks a chart setting. The chart's templates emit every
  variable whether or not it has anything to say, so `django.numProxies: ""` is
  a `CrashLoopBackOff` whose cause is invisible in the diff that caused it.

The second is the one that matters. It is discovered in production, by whoever
is on the end of the pager, on a release that changed something else.

## Decision

**A variable that says nothing is read as one that is not there.** Whitespace
counts as saying nothing. `inventory_tng.environment.Env` reads through a view
of the environment in which such a variable does not appear, so every setting
this application takes falls back to its declared default.

Nothing is lost, because for every setting here "set to nothing" and "not set"
already want the same answer: no extra hosts, no cross-origin readers, no
provider configured. That is not a coincidence — it is what makes an empty
value a plausible thing for somebody to write.

**Where there is no default, the rule makes the refusal stricter rather than
softer.** `DJANGO_SECRET_KEY` and `DATABASE_URL` deliberately have none, so
that a missing one stops the process instead of starting it insecurely. Before
this, an *empty* one slipped through and Django signed sessions with it. Now it
fails at boot exactly as an absent one does, which is what having no default
was always supposed to mean.

**The value is stripped.** `kubectl create secret --from-file` leaves the
file's trailing newline in the value, and a signing key one byte longer than
the operator believes signs every cookie and reset token — until somebody
recreates the Secret from a literal and invalidates all of them at once, with
no diff to point at.

## Consequences

There are two implementations of one rule, and that is deliberate. The
predicate lives in `inventory_tng.options`, which imports nothing outside the
standard library because `console.py` imports it and has to run against a saved
log stream with no application configured; `environment.Env` applies the same
predicate to everything Django reads. One rule, so a reader never has to ask
which behaviour a given variable has, and no import that a reader tool cannot
afford.

`read_env` keeps its documented precedence. The shell wins over the `.env`
file, including when the shell has deliberately cleared a variable — clearing
one for a single command is how somebody turns a thing off, and a file must not
overrule that.
