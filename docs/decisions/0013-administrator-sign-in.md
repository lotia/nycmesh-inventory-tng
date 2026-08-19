# 0013 — Several ways for an administrator to sign in, one way to become one

**Status:** accepted

## Context

[Decision 0012](0012-two-populations.md) requires administrators to
authenticate. It does not say how, and the how matters more than it looks,
because two different questions hide inside it.

The first is **who are you**, and NYC Mesh volunteers arrive with different
answers already in hand. Some live in Slack, which is where the organisation
actually runs. Some would rather use a Google account they already have. Some
want a password they control, and some are on Apple devices and expect Sign in
with Apple. Insisting on one of these excludes people for no benefit.

The second is **what may you do**, and it has exactly one answer regardless of
the first. Conflating them is the failure mode: an application that grants
administrative access on the strength of a successful Google sign-in has made
every Google account holder an administrator.

There is also an availability question. An identity provider that is down, or
an account that is lost, must not lock every administrator out of the system
that records the organisation's stock.

## Decision

**Use [django-allauth](https://docs.allauth.org/) for administrator sign-in,
offering several providers, and grant authority separately.**

1. **Providers, in this order.** Google, Slack, and a generic OpenID Connect
   provider, all of which cost nothing to configure, plus a local username and
   password. Slack is the strongest signal that somebody is actually involved
   in NYC Mesh, and the generic OIDC provider means a future identity provider
   needs configuration rather than code.

2. **A local password path is always retained.** It is the way in when an
   external provider is unreachable or an account is lost, and it is not an
   afterthought — it is the reason the system cannot be locked out of itself.

3. **Second factors on the local path.** `allauth.mfa` supplies TOTP, recovery
   codes and passkeys. TOTP and recovery codes are required for local
   passwords; passkeys are offered. Accounts arriving through a provider
   inherit whatever that provider enforced.

4. **Sign in with Apple is deferred, not refused.** It needs Apple Developer
   Program membership at $99 a year and domain verification, and it is the only
   option on the list that stops working if a subscription lapses. It goes in
   when an administrator asks for it and somebody owns the renewal.

5. **Signing in never grants authority.** Automatic sign-up is off for
   administrative access. A new social account becomes an ordinary user with no
   permissions, and an existing administrator grants the staff flag. Identity
   comes from the provider; authority comes from a person who already has it.

6. **The administrative surface is additionally restricted at the network.**
   Administrators are few and their locations predictable, unlike volunteers,
   who are on a phone wherever stock happens to be. It is the one place a
   network boundary fits without excluding the person this project exists to
   serve. How to configure it is in [deployment](../deployment.md#administrative-access).

## Consequences

- **One dependency covers every path.** allauth speaks all four providers, the
  local password flow, and the second factors, so this is largely configuration
  rather than code.

- **Promotion becomes an operation somebody performs.** Granting the staff flag
  is now a deliberate act with a person behind it, which is what makes point 5
  meaningful. It needs to be doable without a shell.

- **Provider outages degrade rather than block.** Point 2 guarantees a path
  that depends on nothing outside this deployment.

- **Two sign-in surfaces exist and must agree.** The Django admin has its own
  login, and [decision 0014](0014-one-interface.md) puts administrator
  capability in the volunteer application as well. Both authenticate the same
  `User`, so a person promoted once is promoted everywhere.

- **Point 6 constrains deployment, not code.** Its absence is not something the
  application can detect, so it is a precondition somebody has to honour rather
  than a check that fires.

- **The local path is also the only testable one, which is a second reason to
  keep it.** An integration test cannot complete an OAuth round trip against a
  third party, so the browser suite authenticates through point 1's local
  password and its second factor; provider paths get allauth's own test
  helpers. See
  [Integration tests](../../DEVELOPERS.md#integration-tests).

- **Volunteer flows need no authentication in tests at all**, under
  [decision 0012](0012-two-populations.md). The browser suite signs in today
  only because the application has no sign-in of its own; that workaround goes
  away rather than growing.

- **Email addresses from providers are not identity.** Apple in particular
  returns a relay address, and Slack an address the workspace controls. The
  `User` record is the identity; a provider account is a way of proving you may
  use it.

## References

- [django-allauth providers](https://docs.allauth.org/en/dev/socialaccount/providers/index.html)
  — every path in point 1, including the generic
  [OpenID Connect](https://docs.allauth.org/en/dev/socialaccount/providers/openid_connect.html)
  one.
- [allauth.mfa](https://docs.allauth.org/en/dev/mfa/introduction.html) — the
  TOTP, recovery codes and passkeys of point 3.
- [Sign in with Slack](https://docs.slack.dev/authentication/sign-in-with-slack/)
  — Slack's OIDC flow and how to restrict it to one workspace.
- [Sign in with Apple](https://developer.apple.com/sign-in-with-apple/) and the
  [Apple Developer Program](https://developer.apple.com/programs/enroll/) —
  the $99 a year and the domain verification behind point 4.
