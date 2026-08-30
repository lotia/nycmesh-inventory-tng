# 0028 — A certificate a phone will trust, made locally

**Status:** accepted

## Context

Asked by the project owner on 2026-08-30: *"is there a way to automate local
host tls setup. I don't want contributors to have to mess around with openssl
commands to generate certs. I want the end to end flow to be executable
locally, or within containers on a dev machine. There is a strong preference
for containers in a docker or podman compose setup."*

**This is not about a padlock in the address bar.** The reason it matters is
narrower and worse, and
[decision 0011](0011-qr-batch-scanning.md#consequences) already recorded it: a
browser hands the camera only to a secure origin, `localhost` counts as one and
a LAN address does not, and the refusal arrives as a missing API rather than an
error. That bullet is the whole argument and is not repeated here.

So the position today is that the camera — the thing this application exists
to do — works on the developer's own machine at `http://localhost:8080` and
fails on the one device the feature is *for*, in a way that looks like a broken
app rather than a missing certificate. Somebody who wants to work on scanning
cannot, and nothing tells them why.

Nothing in the repository generates, references or ignores a certificate.
`frontend/nginx.conf.template` has one `listen 8080;` and no TLS at all.

**The hard part is not making a certificate.** It is getting a phone to trust
one. A self-signed certificate, or one from a locally generated authority, is
not trusted by an iPhone that has never heard of it, and the browser interrupts
with a warning before the page loads.

Whether *tapping through* that warning is enough is the question this turns on,
and it is the one claim here that has not been tested on a device. Browsers
differ on whether an origin whose certificate was manually overridden counts as
potentially trustworthy, and the behaviour has changed between releases. **This
decision does not rest on it.** It chooses a genuinely trusted root because
that is correct on every platform whatever the answer, and because a workflow
whose first step is "dismiss a security warning" is a poor thing to teach
volunteers on the screen where they type a password.

`inventory-tng-dzwu.2` should establish the answer on a real iOS device early,
because a confirmed *yes* would not change this decision but would change how
loudly the fallback needs documenting.

That is the decision below: not how to make a certificate, but who trusts it
and what that costs the person doing it.

## Decision

**A local certificate authority, run from a container, whose root the
contributor installs once on each device they test from. TLS terminates in a
`profiles:`-gated proxy beside the stack, and every other way of running this
project is unchanged.**

1. **A container makes the authority and the certificate.** No contributor
   types an `openssl` command, and no document contains one. If a reader has to
   copy a line with `-subj` and `-addext` in it, this decision has not been
   implemented — that is the acceptance criterion, in the owner's words.

2. **It is opt-in, through a compose profile.** `docker compose up` continues
   to work for somebody who has not set TLS up, because most contributions do
   not touch the camera. The collector already establishes this pattern
   (`profiles: [telemetry]`), so this is the arrangement the file already
   uses rather than a new one.

3. **TLS terminates in a proxy container, not in the frontend image.** The
   frontend image is the same artifact in every environment and
   [deployment](../deployment.md) leans on that; teaching it to listen on a
   second port for a certificate only a laptop has would make the local image
   and the deployed image differ in the one place they must not.

4. **The port is 8443, not 443.** Measured rather than assumed: this machine
   has `net.ipv4.ip_unprivileged_port_start` at 1024, so a rootless Podman
   container cannot bind 443 without a sysctl change, and asking every
   contributor to make one is exactly the kind of step this decision exists to
   remove. A port does not affect secure-context eligibility — the browser
   cares about the scheme and the host — so `https://10.0.0.5:8443` is a secure
   context and the camera works there.

5. **The certificate carries the address a phone actually dials**, as a
   subject alternative name: the machine's LAN address, and any name the
   contributor prefers. `localhost` alone is useless here, because `localhost`
   was never the problem.

6. **Installing the root on a device is a documented one-time ritual, and the
   documentation says plainly what it costs.** On iOS it is three steps in two
   different places — download the profile, install it under **Settings →
   General → VPN & Device Management**, then enable it under **Settings →
   General → About → Certificate Trust Settings**, which is obscure enough that
   leaving it out would make the instructions wrong rather than brief. It is
   per device, not per checkout, and it survives rebuilding the stack.

7. **A tunnel is the documented alternative, not the default.** Somebody who
   cannot install a root on their device — a managed phone, a borrowed one —
   uses a tunnel service to get a publicly-trusted name. It is an option
   because it genuinely works; it is not the default because it needs an
   account, it sends the traffic off the machine, and it fails exactly where
   NYC Mesh often is, which is a network with no upstream.

## Alternatives considered

**A real certificate for a real name, obtained by DNS-01 and distributed to
contributors.** No device setup at all, and it Just Works. Rejected because it
means shipping the private key for a publicly-trusted name to anybody who
clones the repository. A per-contributor subdomain with a per-contributor DNS
credential avoids that and is a great deal of machinery that somebody has to
administer — which makes the project depend on a person, and
`inventory-tng-dzwu`, the epic this belongs to, exists partly to say it must
not.

**Tunnels as the default.** See point 7. The offline case is not an edge case
for this organisation.

**A WireGuard-style overlay with its own certificate authority.** Same shape as
a tunnel with different trade-offs, and it adds a second network to understand
before the app can be looked at.

**Doing nothing, and testing the camera only on the deployed environment.**
This is the status quo, and it is what makes the scanner the hardest part of
the application to contribute to — you can only work on it if somebody has
given you a deployment. That is the fiefdom the epic's framing warns about.

## Consequences

- **The camera becomes reachable from a phone in local development**, which it
  has never been. That is the whole point, and it is the only consequence that
  matters to the person this is for.

- **A trust decision is made on each device, by hand, once.** This is the cost
  and it is not hidden. It is also the reason the alternative is documented
  rather than omitted: for somebody who cannot make that decision on their
  device, the answer must not be "then you cannot work on this".

- **A private key exists in the working tree, and must never be committed.**
  The path is added to `.gitignore` in the same change that first writes to it,
  never afterwards — a key in history stays in history. This is the one part of
  the implementation that is not merely inconvenient to get wrong.

- **It is one more moving part in an already layered local stack**, and it is
  gated behind a profile precisely so that the people who do not need it never
  meet it. A contributor working on the ledger should not have to know this
  exists.

- **Nothing here reaches the deployed environment.**
  [Deployment](../deployment.md) still requires a real certificate from a real
  authority and offers no switch to skip it. A change that made local
  development easier by making deployment less honest would be refused rather
  than traded.

- **This decision writes no cryptography**, and the implementation may not
  either. Generating a key and signing a certificate is done by an established
  tool through its own interface. `AGENTS.md` rule 3 governs, and reaching for
  anything that looks like implementing a construction is the point at which
  the work stops and asks a person.

## References

- [Decision 0011](0011-qr-batch-scanning.md) — the camera, the secure-context
  requirement, and why `localhost` being exempt is not enough.
- [Secure contexts](https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts)
  — what makes an origin one, and the `localhost` carve-out that does not
  extend to LAN addresses.
- [`getUserMedia`](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia)
  — `navigator.mediaDevices` being `undefined` rather than throwing, which is
  why the failure looks like a bug in the app.
- `inventory-tng-dzwu.2` is the implementation, and is deliberately a separate
  issue: a plan whose implementation was already written is a plan nobody was
  able to disagree with.
