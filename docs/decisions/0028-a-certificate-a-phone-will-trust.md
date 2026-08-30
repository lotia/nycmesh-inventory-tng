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

**A certificate made locally by a container, served by a `profiles:`-gated
proxy beside the stack, with every other way of running this project
unchanged.** *Amended 2026-08-30: self-signed by default, with a local
authority as the documented way to avoid the warning — see
[the amendment](#amendment-2026-08-30--self-signed-by-default-and-the-warning-is-documented).*

1. **A container makes the certificate.** No contributor types an `openssl`
   command, and no document contains one. **Amended 2026-08-30** — it is
   self-signed rather than issued by a local authority; see
   [the amendment](#amendment-2026-08-30--self-signed-by-default-and-the-warning-is-documented).
   The rest of this point is unchanged and is the acceptance criterion. If a reader has to
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
   documentation says plainly what it costs.** **Amended 2026-08-30 — this is
   now optional**, and the default is a self-signed certificate whose warning
   is documented instead. What follows describes the route somebody takes to
   avoid that warning. On iOS it is three steps in two
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

## Amendment, 2026-08-30 — self-signed by default, and the warning is documented

Point 1 above chose a local certificate **authority**, whose root a contributor
installs once on each device, precisely so that no warning ever appears. Point 6
accepted the per-device trust ritual as the cost of that.

**The default is now a self-signed certificate, and the browser warning is
expected.** The owner's call, and his words:

> I'm okay with self signed cert by default since developers are/should be
> aware that the browser will present a warning. I'd also like that clearly
> indicated in the relevant docs.

**The reasoning, which is the part worth keeping.** The audience for the local
stack is developers, not volunteers. A warning about a certificate your own
machine made a minute ago, on a hostname you typed yourself, is information
rather than a threat — and somebody working on this project can be expected to
read it. Against that, the trust ritual is real work repeated on every device,
and what it buys is the absence of a dialog the person seeing it already
understands. That is a poor trade for the common case, and it is the operator's
trade to make.

It also removes the single worst step in the original plan: the iOS ritual is
three actions in two places, obscure enough that this record had to spell them
out, and every one of them is a chance for somebody to give up before they see
the app.

**What does not change, and must not be lost.** The subject of this record was
never the warning. It was that the camera is the reason any of this exists, and
that **clicking through a warning may not yield the secure context
`getUserMedia` requires** — a claim this record states plainly as untested. That
stays untested, and it now sits underneath the default rather than beside it:

- If clicking through **does** give a secure context, self-signed is the right
  default and the authority is an optional comfort.
- If it **does not**, then self-signed alone does not achieve the goal on that
  platform, and the authority is the answer there rather than an upgrade.

So `inventory-tng-dzwu.2` still settles it on a real iOS device early. It is now
the first thing that issue does rather than a detail within it, because the
answer decides whether the documented default works for the feature it exists
to serve.

**The local authority stays documented**, as the route for anybody who wants no
warning, who is demonstrating the app to somebody who should not be taught to
dismiss security dialogs, or who finds the camera refuses. It moves from
"the default" to "the way out", and no work is discarded.

**And the warning is written down where somebody meets it.** Not as a footnote:
in the words a person seeing it would recognise, at the point in the setup where
it appears, saying what it is and that it is expected. A warning nobody was
warned about is the same defect as a camera that fails silently — it teaches the
reader that this project is broken, at the moment they are deciding whether to
keep going.

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
