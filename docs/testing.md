# Testing and coverage

**Every change that adds code adds tests for it, and CI fails if it does not.**
How to run the suites, what the integration suite can see that the unit suites
cannot, how the guides' pictures are made, and what the coverage threshold is
for. Which of this repository's documents CI executes, and how far, is
[What CI proves](../DEVELOPERS.md#what-ci-proves) rather than this page.

## How to run them

```bash
cd backend  && uv run pytest    # pytest + coverage
cd frontend && npm test         # vitest + coverage
```

Coverage is built into both commands rather than being a separate CI-only step.
A local run and a CI run enforce exactly the same rules, so the build cannot
fail on something you had no way to see.

## Integration tests

```bash
cd frontend && npm run test:integration    # Playwright, a real browser
```

Separate from the commands above on purpose, and not part of the coverage
threshold. They start a real Django, a real Vite dev server and a real browser,
and assert through a real browser's cookie jar and origin checks.

They are not a production rehearsal: they run the dev server rather than the
nginx image, with `DJANGO_DEBUG` on, so the secure-cookie and HSTS behaviour
that only appears with `DEBUG` off is still untested.

They exist because the unit tests cannot see a whole class of bug. Django's
test client exempts itself from CSRF and jsdom is not a browser, so both suites
were once green against an API no browser could write to — twice over, in fact:
nothing set a CSRF cookie, and the dev server's origin was not trusted. Only
this suite can fail on the second, and it is the only one that exercises either
through a real browser.

The scanner is here for the same reason. Self-hosting the decoder's `.wasm` is
a non-optional constraint of
[decision 0011](decisions/0011-qr-batch-scanning.md), and whether it holds
is a question about a request a browser makes — jsdom has no camera to open and
the CDN's URL is still in the built JavaScript as a default nothing reaches, so
neither the unit suite nor a look at the bundle can answer it. The camera is
opened against Chromium's fake device, which needs no flag of yours: the spec
asks for it.

One of them decodes a symbol through a browser, which no other test can. What
nothing could reach until now is the handoff between a camera frame and the
decoder, where a scanner can decode nothing while every other suite stays
green. `integration/decodes.spec.ts` closes that; its header says what the
other suites already settle, and what it found when it first ran. The clip it
films is generated during the run rather than committed, and needs no ffmpeg
and no container; how, and why, is in `frontend/integration/qrVideo.ts`.

The offline queue is here on a variant of the first argument.
`integration/offline-batch.spec.ts` throws away a batch's *answer* instead of
its request, so the browser retries something the ledger already holds — and
whether that writes a second row is a question only a real ledger can be asked.
Both sides of it are covered without a browser and neither can see the join.

They need Docker (for PostgreSQL) and a one-off browser download:

```bash
cd frontend && npx playwright install chromium
```

Servers, migrations and the fixed test scene are all handled by the suite
itself, so there is no separate setup step, and a server you already have
running is reused without changing that. The scene comes from
`manage.py seed_integration_data`, which creates a login whose password *and*
whose TOTP secret are written down in this repository and so refuses to run
unless `DJANGO_DEBUG` is on *and* it is passed the flag that acknowledges that.
Running it by hand means typing that flag; the command says why.

The suite signs in through the local password path of
[decision 0013](decisions/0013-administrator-sign-in.md) and completes the
real second factor, computing the code from that published secret with `pyotp`.
An OAuth round trip to Google or Slack cannot be completed from CI, so the
provider paths are covered in the backend suite instead, where a callback can
be finished without dialling anybody.

They write to your development database rather than a throwaway one, because
the point is to exercise the servers you actually run.

## The guides' screenshots

```bash
cd frontend && npm run capture:guides
```

Every picture in [guides/volunteer.md](../guides/volunteer.md) and
[guides/administrator.md](../guides/administrator.md) comes from that command
rather than from somebody's phone, which is what makes them regenerable. It
drives the same servers and the same seeded scene as the suite above — its
config spreads `playwright.config.ts` rather than restating it — and writes one
PNG per step into `guides/images/`.

Kept out of `npm run test:integration` on purpose. A run of it rewrites every
PNG under `guides/images/`, and those are then committed — a suite that edits
the working tree is not a suite. CI does not run it either.

What it adds to that scene — the stickers to scan, stock on a shelf, something
measured whose scan asks how much, and the questions the sheet import leaves
behind — is in `frontend/capture/scene.ts`, and every code and quantity in it
is fixed, so a run against an unchanged app rewrites almost nothing. Two
pictures do change every time and cannot not: one is of the movements, which a
run appends to and nothing may edit, and one carries the date it was printed.
Which pictures exist at all is `frontend/capture/shots.ts`, and `npm test`
fails when one of them is missing from `guides/images/`, when the guide that
claims it does not draw it, and when `guides/images/` holds a PNG no shot
claims.

`capture/` is measured by the coverage thresholds like anything else. The three
files in it that only a browser can reach — the driver, the gestures against a
live `Page`, and the scene, which shells out to `manage.py` — are excluded by
name in `vite.config.ts`, with the reason beside them.

Run it when you change a screen one of them shows, and commit the PNGs with
that change.

## What breaks the build

Both of these fail the command with a non-zero exit code, and therefore fail CI:

1. **Any failing test.**
2. **Coverage below the threshold** — currently **90%** on both sides, applied
   to lines, and additionally to branches, functions, and statements on the
   frontend.

## Why 90% and not 100%

Chasing 100% pushes people into writing tests for code that cannot
meaningfully break, which wastes effort and produces tests nobody maintains. The
number is not the point.

The real work is done by the **exclusion lists**, which decide what counts as
code worth covering. Everything not excluded is expected to be tested, and 90%
leaves only a little room for the genuinely awkward case.

| Excluded | Where | Why |
| --- | --- | --- |
| `manage.py`, `wsgi.py`, `asgi.py`, `gunicorn.conf.py` | `backend/pyproject.toml` → `[tool.coverage.run] omit` | Entry points run by Django or the server, never by tests. `gunicorn.conf.py` computes nothing of its own — what it calls is covered |
| `settings.py` | same | Declarative configuration. Every test imports it, so counting it would inflate the percentage without testing any behaviour |
| `migrations/` | same | Generated by `makemigrations` |
| `src/main.tsx` | `frontend/vite.config.ts` → `test.coverage.exclude` | Bootstrap that mounts React onto the DOM; no behaviour of its own |
| `src/theme.ts` | same | Declarative configuration, as with `settings.py` |

If you are tempted to add an exclusion, say why in the pull request. Excluding a
file is a decision about what does not need testing, and it deserves the same
scrutiny as the code itself. Lowering a threshold needs a reason too; raising
one needs nothing but a green build.

## Writing tests

| | Backend | Frontend |
| --- | --- | --- |
| Framework | [pytest](https://docs.pytest.org/) with `pytest-django` | [Vitest](https://vitest.dev/) |
| Location | `backend/src/inventory/tests/` | next to the code, as `*.test.tsx` |
| Component testing | — | [Testing Library](https://testing-library.com/docs/react-testing-library/intro/) |
| Database access | needs `@pytest.mark.django_db` | — |

Test behaviour through the public surface — an API endpoint's response, what a
user sees rendered — rather than asserting on internals. Tests written that way
survive refactoring, which is the only reason they are worth having.

