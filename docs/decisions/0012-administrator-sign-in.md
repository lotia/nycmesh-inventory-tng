# 0012 — How administrators sign in

**Status:** accepted · 2026-11-02 · amends 0004 (TOTP may be optional in
production) · amended 2026-11-10 (retiring removes the sign-in; nobody is
deleted; a code for each change to another administrator, and everyone
told)

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
- **Attempts are rate limited**, as registration is (0009), in the
  database, shared by every instance (Amendment):
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
- **Another administrator can reset an account's sign-in.** At once, its
  password goes, its authenticators are retired, its recovery codes and
  other links are voided and its sessions end, so nothing a thief may hold
  works, even if the link is never used. They are shown a link that sets a
  new password, which works once, lasts 24 hours, and is passed on however
  they like; TOTP is set up again at sign-in. It always resets both: the one
  acting rarely knows which factor leaked, and someone who only forgot their
  password can reset it by mail. Asking by mail is a separate flow that
  changes nothing until its link is used, as anyone may ask, and makes no
  link while the account has no password: otherwise a mailbox alone would
  undo the reset. One who never chose a password has nothing to reset and
  gets a new invite instead.
- **Administrators are retired, never deleted**, and can always be brought
  back. Their counts, adjustments, links and history keep pointing at the
  account, and the administrators page lists retired ones apart. We
  rejected deleting them: the ledger names who counted, and a deleted
  account would leave those rows pointing nowhere.
- **Retiring removes everything they sign in with**, as a reset does, and
  voids their links, so a retired account holds no password hash to crack
  and no TOTP key, even in a backup. Bringing them back makes a link that
  works as an invite: they choose a new password and set up TOTP again,
  whatever they were retired for. We rejected keeping the password until
  they come back: nothing could use it meanwhile, but a leaked database
  would still hold it.
- **Resetting, retiring or bringing back another administrator asks for a
  code each time**, typed with the form, whether or not a recent one is
  still open, so a stolen session cannot do it without the code too. The
  form is sent once: sent again, it is answered so before a code is asked
  for, so none is spent on it. An
  honest administrator can still do several in a minute. We rejected a
  limit per hour on these: it held back the administrators trying to
  retire a malicious one as much as it slowed the attacker, and cannot
  stop an administrator with their own code at all. That is the
  operator's to stop.
- **Every administrator is told of these changes**, of an invite, of a
  link made with the recovery token and of a change to the sign-in
  settings: mailed if they gave an address (the
  one who did it too, in case it was not them; not the one it was done to,
  who gets their own mail), and shown on the administrators page ("Recent
  changes") and, for what others did since they last signed in, on the
  home page, so those without an address hear of it too. Each is kept in
  `activity`, which the app may only add to, by kind, who did it and to
  whom, for good. A new kind is a name, its words (activity.py) and its
  migration's check.
- **Only an administrator who can still sign in changes another.** A
  request already waiting when its session was cut off, by a reset or by
  retiring, changes nothing. So two resetting each other at once leave one
  who can sign in, and as nobody acts on themselves, someone who can sign
  in always remains: there is no separate check for the last one.
- **Nothing already under way undoes a reset, retire or restore.** Every
  sensitive form takes the same lock they do, every administrator's row in
  one order, so it waits for one in progress; then it reads its session,
  the account and the settings again, and goes to sign-in, or asks for a
  code, if they changed. Checking a password is done with nothing locked,
  then the account is locked and the same password must still be there.
  Every path locks the account's own row before anything else of it, so
  no two of them deadlock. Sensitive forms therefore run one at a time;
  with a handful of administrators, nobody waits long. A page view or an
  ordinary change already under way still finishes.
- **The operator can let a locked-out administrator back in** by setting
  `RECOVERY_TOKEN` in the deployment settings, for when nobody else can: a
  lone administrator with no email, say. While it is set, `/recover` takes
  it and a username and makes a reset link for the password and
  authenticators, which lasts an hour. Each token works once: its hash is
  kept on the link. It is rate limited, recorded in the history and logs,
  and mailed to the administrator if they have an address. We rejected a
  server command: on Kubernetes that means exec into a pod, which GitOps
  setups often forbid, or a Job whose logs would keep the link.
- **An administrator with an email can reset their own password** from the
  sign-in page. The page answers the same whether or not a mail was sent.
  At most three are sent an hour per account, and asking never voids a link
  another administrator or the recovery token made.
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
  history shows who made it, and every administrator is told.
- Anyone who reads the database can copy the TOTP keys, until they are
  encrypted at rest (inventory-tng-8i4.48).
- The page's words never reveal that a password was right, but its timing
  can: a right password goes on to a few more database queries. What that
  tells an attacker is worth at most ten code guesses every 15 minutes.
- An administrator who could change the TOTP policy could weaken it; the
  history and the email to every administrator are what make that visible.

## Amendment: limits shared by every instance (2026-11-09)

Kept per process, the limits allowed each pod and worker its own attempts:
with P processes, P times as many guesses at a code an administrator's
password already unlocks (inventory-tng-8i4.124). They are now kept in
PostgreSQL, through the `limits` library's moving window over a storage of
our own (rate_limits.py): one statement per attempt, atomic across
instances, timed by the database's clock, committed apart from the request
so a refused attempt still counts, and deleted once its window passes.

We rejected Valkey (a new service to run, and sign-in down whenever it is),
PyrateLimiter's PostgreSQL bucket (a table per key, made at runtime, and a
table lock that refuses attempts under the limit when instances meet), and
a counter of our own without `limits` (it would change every call site and
lose the moving window for about 50 fewer lines).
