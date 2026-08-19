# Getting off Django 6.1 in time

A plan, not a change. [Decision 0003](../decisions/0003-django-version.md) chose
Django 6.1 over the 5.2 LTS and is authoritative on *why*; this document is the
other half of that bargain — *when* the bill comes due, and what has to be true
before it can be paid. It is tracked as bead `inventory-tng-9u8`.

Everything below was verified on **2026-08-19** against the sources named. Check
them again before acting: this is a document about the future, and the future
moved once already (see [What has changed](#what-has-changed-since-this-was-first-written)).

---

## The dates

From the [Django download page](https://www.djangoproject.com/download/), which
is the project's own supported-versions and roadmap table:

| Series | Released | Mainstream support ends | Extended (security) support ends |
| --- | --- | --- | --- |
| 5.2 LTS | Apr 2025 | 3 Dec 2025 | April 2028 |
| 6.0 | 3 Dec 2025 | 4 Aug 2026 | April 2027 |
| **6.1 — ours** | **5 Aug 2026** | **April 2027** | **December 2027** |
| 6.2 LTS | April 2027 (expected) | December 2027 | April 2030 |

The 6.1 release date is from the
[release announcement](https://www.djangoproject.com/weblog/2026/aug/05/django-61-released/).
There is no `Version6.2Roadmap` wiki page yet, so "April 2027" for 6.2 is the
download page's roadmap row and not a fixed day.

Two dates matter and they are not the same one:

- **April 2027** — 6.2 LTS ships and 6.1 stops getting bug fixes. This opens the
  window; it is not a deadline.
- **December 2027** — 6.1 stops getting *security* fixes. This is the deadline.
  Running an unpatched Django after this is the thing to avoid.

That leaves roughly an eight-month window. Aim at the front of it: an LTS is
worth being early for, and the risk here is not Django but the packages around
it (below), which historically need a few months.

## What has changed since this was first written

The bead said "before April 2027", which conflated the two dates above. April
2027 is when 6.2 arrives; 6.1 is security-supported until **December 2027**. The
real deadline is four months later than the bead implied.

Separately, and more interestingly: the download page now states that **Django
6.2 is the final release under the current versioning and support policy**.
From **January 2028** Django switches to `YYYY` versions released every January,
and *every* feature release gets the full three-year support period.

That retires the trade-off decision 0003 was written about. After 6.2 there is
no LTS to choose, because there is no non-LTS to choose it over. Decision 0003
should be revisited at that point — not to reverse it, but because its
"Consequences" section will describe a world that no longer exists. That is a
separate follow-up, not part of this upgrade.

## What gates the upgrade

Django itself is the easy part. The pinned third-party packages that reach into
Django's internals are what actually decide the date. Current pins are in
[`backend/pyproject.toml`](../../backend/pyproject.toml); the "declares" column
is the highest `Framework :: Django :: X.Y` trove classifier published on PyPI
as of 2026-08-19.

| Package | Pinned | Declares | Reads Django internals? | Track record |
| --- | --- | --- | --- | --- |
| `djangorestframework` | `3.18.*` | 6.1 | Yes, deeply | 6.1 support two days after Django 6.1; 6.0 took ~3.5 months |
| `drf-spectacular` | `0.30.*` | 6.0 | Yes — introspects DRF and Django | **Slowest of the set.** 6.0 support arrived ~7 months late |
| `django-simple-history` | `3.*` | 6.0 | Yes — builds historical models | 6.0 within 8 days; no 6.1 release yet |
| `django-filter` | `25.*` | 6.1 | Some | Ships *before* Django finals |
| `django-allauth` | `>=65.19.1` | 6.1 | Yes — auth stack | Prompt; 6.1 the day after release |
| `django-cors-headers` | `4.9.*` | 6.0 | Middleware only | 6.1 support merged but unreleased |
| `whitenoise` | `6.11.*` | 6.0 | Staticfiles storage backend | 6.1 support merged but unreleased |
| `django-environ` | `0.12.*` | 6.0 | No — settings parsing | Version-agnostic in practice |
| `psycopg[binary]` | `3.3.*` | none | No — database driver | Not tied to Django versions |
| `django-stubs` | `5.*` | 6.1 (in 6.1.0) | Type stubs only | Version-tracks Django |
| `djangorestframework-stubs` | `3.16.*`¹ | inherits | Type stubs only | Follows `django-stubs` within weeks |

¹ Pinned as `3.*`; the lock currently resolves to 3.16.9.

**Watch these three.** They are the ones that have historically made people
wait:

1. **`drf-spectacular`** — the worst lag by a wide margin, and the project's
   OpenAPI schema is asserted by a test
   ([`The API schema`](../../DEVELOPERS.md#the-api-schema)), so a version bump
   here is visible whether or not Django changed.
2. **`django-simple-history`** — generates a parallel model and migration for
   every audited model. Django model-internals changes land here first.
3. **`django-allauth`** — the whole administrator sign-in path
   ([decision 0013](../decisions/0013-administrator-sign-in.md)) runs through it.

**Also note, unrelated to 6.2:** `django-stubs` is pinned to `5.*` and resolves
to 5.2.9, so the type stubs describe Django 5.2 while the application runs
Django 6.1. `django-stubs` 6.1.0 exists. Bumping the stubs is worth doing on its
own schedule and would be a sensible thing to fold into this upgrade, since both
are "make the type checker agree with the Django we actually run".

## What in this repository is likely to need work

Less than you would expect, for a specific reason: Django removes a deprecated
feature two feature releases after deprecating it, and the
[6.1 deprecation timeline](https://docs.djangoproject.com/en/6.1/internals/deprecation/)
lists **no removals scheduled for 6.2**. Everything deprecated in 6.0 and 6.1 is
slated for the release *after* 6.2. So 6.2 should be additive for us, and the
things deprecated now become the 6.2-to-next-release problem instead.

Checked on 2026-08-19 against the items on that timeline, `backend/src` uses
none of the following: the `EMAIL_*` settings, `send_mail`, `get_connection`,
`EmailMessage`, `ADMINS`/`MANAGERS`, `StringAgg`, `BLANK_CHOICE_DASH`, or
`values_list(flat=True)` without a field. The one `select_related()` call
(`inventory/views.py`) passes field names, which is the form that survives.

And the suite is currently silent on deprecations:

```bash
cd backend && uv run pytest -W error::DeprecationWarning
```

produces no `DeprecationWarning` failures today. Re-run it after each bump —
this is the cheapest early warning available, and worth running once against
6.2's alpha rather than waiting for the final.

The parts most likely to actually move:

- **The committed OpenAPI schema.** `backend/openapi.yaml` is asserted equal to
  generated output. A `drf-spectacular` bump alone can change it; regenerate and
  read the diff rather than accepting it. See
  [`The API schema`](../../DEVELOPERS.md#the-api-schema).
- **Migrations.** `django-simple-history` upgrades have historically produced
  historical-model migrations. `manage.py makemigrations --check --dry-run`
  after the bump, and read anything it wants to write.
- **Static files.** `whitenoise` is the staticfiles storage backend; Django
  changes to `STORAGES` land here.
- **`ty` and the stubs.** New Django means new stubs means possibly new or
  newly-unnecessary suppressions — see
  [`Typing`](../../DEVELOPERS.md#what-the-checker-cannot-see) and bead
  `inventory-tng-61b`.
- **The Python floor.** [`mise.toml`](../../mise.toml) pins Python 3.14. Confirm
  6.2's supported Python versions when its release notes appear; Django
  regularly raises the floor at an LTS.

## The sequence

1. **~January 2027**, when Django 6.2 alpha appears: read its release notes and
   deprecation timeline, and re-check the three gating packages above for a
   6.2-declaring release or an open issue. Nothing needs changing yet.
2. **April 2027**, on the 6.2 final: if `drf-spectacular` and
   `django-simple-history` have shipped 6.2 support, do the upgrade. Bump
   `django==6.2.*` in `backend/pyproject.toml` together with the two stub
   packages, `uv lock`, then the ordinary gates — `uv run ruff check --fix . &&
   uv run ruff format .`, `uv run ty check src`, `uv run pytest`, plus
   `makemigrations --check --dry-run` and a schema regeneration.
3. **If they have not shipped**, wait, and re-check monthly. There is slack
   until December 2027 and no benefit to forcing it.
4. **By September 2027 at the latest**, escalate: three months of security
   support left is the point at which a workaround (pinning a fork, dropping a
   dependency) becomes cheaper than the risk of running unpatched.
5. **Update [decision 0003](../decisions/0003-django-version.md)** in the same
   change — its "Consequences" section names the upgrade as tracked work, and
   that sentence stops being true when it is done.
