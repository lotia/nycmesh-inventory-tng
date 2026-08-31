# Who can write to the ledger

A decision brief, written for collaborators rather than implementers. It is the
long-form version of decisions [0012](../decisions/0012-two-populations.md),
[0013](../decisions/0013-administrator-sign-in.md) and
[0014](../decisions/0014-one-interface.md), which are authoritative wherever the
two disagree. It exists because those records state what was decided, and this
states what was considered and rejected on the way.

A presented version of this document is published as an
[artifact](https://claude.ai/code/artifact/1068ede9-c89f-4182-8970-2ff2f3a0871a),
which requires an account on that service; this file is the copy that does not.

---

## What has been built since, and what it changed

Added 31 August 2026. This brief was written before any of it existed, and three
of its answers have since become code rather than intentions. The reasoning below
is unchanged; what follows is where to look for the running version.

| Question | Where it now lives |
| --- | --- |
| Q2, two populations | [0012](../decisions/0012-two-populations.md), and `VOLUNTEER_ACCESS` in [access.py](../../backend/src/inventory_tng/access.py) is the switch that makes point 3 true |
| Q3, administrator sign-in | [0013](../decisions/0013-administrator-sign-in.md) and its 2026-08-30 amendment, which hands the second-factor requirement to the operator |
| Q4, the volunteer path | Read limits now exist beside the write ones — `AnonymousReadThrottle` in [throttling.py](../../backend/src/inventory/throttling.py) |
| Q6, where administrators work | [0014](../decisions/0014-one-interface.md), and [0027](../decisions/0027-the-admin-is-meant-to-look-different.md) on why the Django admin is left looking unlike the app |

Two of those settings are marked as debts rather than permanent choices, meaning
they exist to defer a decision and are meant to be deleted once it is taken.
Which is which, and what would end each one, is
[postures.py](../../backend/src/inventory_tng/postures.py).

## Q1. Can the backend tell our frontend apart from any other client?

**The proposal.** The backend holds a list of allowed origins. If a request
arrives from a frontend served by one of them, it is granted a token, refreshed
quietly in the background so nobody ever sees a login.

**Not viable.** `Origin` is a request header. The browser sets it honestly and
will not let page JavaScript forge it, but anything that is not a browser can
send whatever it likes:

```
$ curl -X POST https://inventory.nycmesh.net/api/auth/token \
     -H 'Origin: https://inventory.nycmesh.net'

{"access_token": "...", "expires_in": 3600}
```

A backend that issues tokens on the strength of that header issues them to
anyone who asks. Inspecting the TLS certificate does not rescue it: no browser
API lets a page prove which certificate served it, and the backend already knows
its own.

**What survives.** The allow-list is still worth having. It stops *another
website* using a volunteer's browser against the API. It is simply not a way of
identifying the client.

## Q2. One group of users, or two?

**Decided: two.** Volunteers append to a ledger that cannot be rewritten,
attributed to a name they pick. Administrators do everything that can change or
destroy what is already recorded — editing the catalogue, merging volunteers,
revoking labels, correcting the ledger.

This is the answer that makes the rest tractable, because it bounds what the
no-login path can cost. The worst an abused volunteer client can do is write
rows that are wrong, which an identified administrator then compensates.
Nothing is destroyed and nothing is silently altered.

## Q3. How does an administrator prove who they are?

**Decided: four paths now, Apple on request.** All from
[django-allauth](https://docs.allauth.org/).

| Path | Cost | Decision |
| --- | --- | --- |
| Google | Free | Now |
| Slack | Free | Now — restrictable to the NYC Mesh workspace |
| OpenID Connect (generic) | Free | Now — the current name for "OID" |
| Username, password, TOTP | Free | Now, and kept whatever else is added |
| Sign in with Apple | $99/year | Deferred — needs Developer Program membership and renewal |

The distinction the decision turns on: signing in with Google proves *who you
are*. It must never by itself make you an administrator, or everyone with a
Google account is one. Identity comes from the provider; authority is granted by
somebody who already holds it.

## Q4. What protects the volunteer path, which has no login?

**Decided: open for now, recorded as a choice.** Rate limits, an append-only
ledger, attribution on every row, and an administrator who can compensate
anything wrong. Device enrolment follows once the flow is in real use
(`inventory-tng-jro`).

Built since, and worth stating because the answer above was thinner than it
reads: the limits this names were on writing only. Safe methods were exempt on
purpose, since the endpoint taking a volunteer's name is also the list searched
as somebody types. So an open reading surface was covered by nothing at all
until `inventory-tng-81f7.1`, which added a limit sized against a different
attack — asking whether one address belongs to a volunteer, rather than copying
the roster. `AnonymousReadThrottle` carries that argument.

Rejected alternatives, and why:

- **Network gating for volunteers** — the strongest boundary, but it excludes a
  phone on a mobile network in a basement, which is the device this project is
  designed around. It is applied to the administrative surface instead.
- **A shared secret in the frontend** — a secret shipped to every user is not a
  secret.
- **Anonymous attestation** (Privacy Pass) — real, standardised, and
  disproportionate here; it needs an issuer to trust.

A limit worth knowing in advance: a key the browser cannot export stops somebody
walking off with the credential, but not script on the page asking that key to
sign. Once script runs on your origin, no frontend mechanism stops it obtaining
valid credentials. That is a job for a Content-Security-Policy.

## Q5. How does somebody on a laptop enrol, with no camera?

**Decided: the secret is a string; the QR is one way to type it.** Three routes,
none needing a camera: type it (the Crockford alphabet in decision 0011 exists
so a hand-copied code resolves), follow a link, or ask an administrator to
approve a pending device.

A desktop user probably never needs a camera at all — the primary screen is a
list of items with `−1 / count / +1` beside each.

## Q6. Where do administrators actually work?

**Decided: the same application, with Django's admin kept as a fallback.**
What that means in the interface is
[decision 0014](../decisions/0014-one-interface.md); what it cost is below.

The cost, stated plainly: this re-couples what Q2 separated. Putting
administrative capability into the volunteer app means script injected into that
app reaches the destructive operations too. It does not make the decision wrong
— an administrator using a separate app is equally lost if that app is
compromised — but it makes a strict Content-Security-Policy and
re-authentication before destructive operations requirements rather than good
practice.

## What this created

Sequenced so the first three unblock the submit button:

1. Administrator sign-in (`inventory-tng-axt`)
2. Rate limiting the two open endpoints (`inventory-tng-csm`)
3. The catalogue write API (`inventory-tng-0rr`) and a capabilities endpoint
   (`inventory-tng-wfx`)
4. Administrative powers in the app (`inventory-tng-oji`)
5. Device enrolment (`inventory-tng-jro`)

## Sources

- [OAuth 2.0 for Browser-Based Applications](https://www.ietf.org/archive/id/draft-ietf-oauth-browser-based-apps-26.html)
  — IETF; sections 5.1 and 6.3.3 decide Q1.
- [CORS Origin Header Scrutiny](https://owasp.org/www-community/attacks/CORS_OriginHeaderScrutiny) — OWASP.
- [RFC 9421: HTTP Message Signatures](https://www.rfc-editor.org/rfc/rfc9421.html)
  — one signing scheme for browsers and machine clients alike.
- [django-allauth providers](https://docs.allauth.org/en/dev/socialaccount/providers/index.html)
  and [its MFA app](https://docs.allauth.org/en/dev/mfa/introduction.html).
- [Sign in with Apple](https://developer.apple.com/sign-in-with-apple/) and the
  [Developer Program](https://developer.apple.com/programs/enroll/).
- [Device Bound Session Credentials](https://developer.chrome.com/docs/web-platform/device-bound-session-credentials)
  — the browser-native form of Q4's enrolment.
- [The DPoP Storage Paradox](https://www.infoq.com/articles/dpop-key-storage-unsolved-problem/)
  — the signing-oracle limit.
- [Good Practices for Capability URLs](https://www.w3.org/2001/tag/doc/capability-urls) — W3C TAG.
- [Crockford's Base32](https://www.crockford.com/base32.html) — the alphabet behind Q5.
