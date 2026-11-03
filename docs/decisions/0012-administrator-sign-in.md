# 0012 — How administrators sign in

**Status:** accepted · 2026-11-02 · amends 0004 (TOTP may be optional in
production)

## Context

Administrators change the catalogue, register devices and manage each other
(0004). They have no shell, server or code access, so everything about their
accounts, from the first one onwards, happens in the browser. Some will be
on shared machines, such as a hub laptop. The main users have not yet been
asked whether TOTP should be required, so the policy must be changeable
without a deployment.

## Decision

### Accounts

- **An administrator signs in with a username**, unique ignoring case. An
  email address is optional, and is only used for password resets and
  notices. Passwords are hashed with argon2-cffi; TOTP uses pyotp.
- **The first administrator comes from a setup page.** While no administrator
  exists, `/setup` asks for the setup token the operator put in the deployment
  settings (`SETUP_TOKEN`), then a username, a password and, unless TOTP is
  optional, an authenticator. Once any administrator exists, the page is
  gone for good. Later administrators are invited by existing ones.

### Signing in

- **One form: username, password and code.** The code field also takes a
  recovery code. Every refusal says the same thing, whichever part was
  wrong, and the code field is shown to everyone, so the page never reveals
  that a password was right or which accounts use TOTP. An unknown username
  is checked against a dummy hash, so it takes as long as a known one.
- **Attempts are rate limited**, per process, as registration is (0009):
  every attempt per address, and codes per administrator, counted only once
  the password was right. Without the password, nobody can lock an
  administrator out by their username.
- **Requests from another site's page are refused**, on every route that
  changes something, using the browser's `Sec-Fetch-Site` header, or
  `Origin` where that is missing, instead of CSRF tokens.

### TOTP

- **The policy is `required` (the default) or `optional`.** While it is
  optional, each administrator may turn TOTP on or off for themselves. When
  it becomes required, an administrator without TOTP who signs in can only
  set it up until they have.
- **Setting up shows a QR code** (segno) and the same key as text, and saves
  the authenticator only once a code from it is typed back. Each
  authenticator has a name and refuses a code from a time step it has
  already accepted, or an earlier one.
- **An administrator may have several authenticators.** To change phones,
  add the new one, confirm it, then remove the old. While the setup page is
  open, the key may go into as many apps as wanted. A saved key is never
  shown again: anyone in a signed-in session could copy it.
- **Ten recovery codes** are shown once, when TOTP is first set up. Each works
  once in place of a code. Making new ones voids the old. Each is 20
  characters, about 98 bits, so the SHA-256 kept of it cannot be guessed
  back from a stolen database, as a shorter code's could. The ten-character
  codes made at first were voided (migration 0008).

### Sessions

- **Sessions are kept on the server**, behind a cookie whose SHA-256 hash is
  stored, as devices are (0009). They last a week by default, used or not,
  with no idle limit.
- **Sensitive actions need a recent code** (or the password, without TOTP):
  within 24 hours, and within an hour of the last sensitive action done
  (viewing a page does not count). When the window has closed, the page
  asks for a code and carries on; it never signs anyone out. Sensitive
  actions are anything about administrators, one's own password, email,
  authenticators and recovery codes, the sign-in settings, registration
  codes and revoking devices.
- **An administrator can see their sessions** and end any or all of them.
  Changing their password ends their other sessions; a reset, or being
  retired, ends them all. Changing the password or email also voids any
  unused reset link, and an email change is told to the old address.

### Settings and resets

- **The TOTP policy and the three times live in the database**, in one row
  that administrators change from a page. Changing them is a sensitive
  action, is recorded in the history, and is emailed to every administrator
  with an address.
- **Another administrator can make a reset link**, for the password, the
  authenticators or both. It works once, lasts 24 hours, and is passed on
  however they like. Using it ends the account's sessions.
- **An administrator with an email can reset their own password** from the
  sign-in page. The page answers the same whether or not a mail was sent.
  At most three are sent an hour per account, and asking never voids a link
  another administrator made.
  The link works once, lasts an hour and resets the password only: TOTP is
  still asked for. Mail is sent after the reply, so its timing reveals
  nothing.
- **Mail goes over authenticated SMTP** to a provider (Mailgun or Gmail, for
  example), with the standard library's `smtplib`, set in the deployment
  settings. Without them, emailed resets and notices are off. Locally,
  Mailpit catches every message.
- **Only hashes of tokens and codes are stored.** TOTP keys cannot be hashed,
  so they are stored as they are, and wiped when an authenticator is
  removed. No secret goes into the history.

We rejected email as the sign-in name (more personal data, and no mail to
rely on), a two-step sign-in (it tells a guesser the password was right),
showing a saved TOTP key again, and putting the TOTP policy in the
deployment settings, where administrators could not change it.

## Consequences

- With TOTP optional, an account is protected only by its password until its
  administrator turns TOTP on.
- A week-long session is longer than NIST SP 800-63B-4 suggests for two
  factors (24 hours); the 24-hour window for sensitive actions is what keeps
  to it. Those are the defaults: administrators can raise both from the
  Settings page, to 400 days and a week. A session left open on a shared
  machine can still change the catalogue for a week, unless ended from the
  account page.
- Any administrator can take over another's account with a reset link. The
  history shows who made it.
- Anyone who reads the database can copy the TOTP keys, until they are
  encrypted at rest (inventory-tng-8i4.48).
- The page's words never reveal that a password was right, but its timing
  can: a right password goes on to a few more database queries. What that
  tells an attacker is worth at most ten code guesses every 15 minutes.
- An administrator who could change the TOTP policy could weaken it; the
  history and the email to every administrator are what make that visible.
