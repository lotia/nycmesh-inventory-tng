# 0030 — The network is the access control, and the application is defence in depth

**Status:** accepted

## Context

`mesh_only` was written as an application check. The spike on
`inventory-tng-81f7.3` implemented it as a permission class comparing the
caller's address against a configured list of networks, and the demonstration
brief, the run of show and `.env.sample` all described it that way.

Read like that it is a weak control, and it was argued against on exactly those
grounds: an address a request claims for itself is a claim about the deployment
rather than a fact about the caller, which is what
[0023](0023-which-addresses-may-speak-for-another.md) exists to say. That
argument was sound and it was aimed at the wrong thing.

**What is meant, recorded from the project owner on 2026-08-25.** The deployment
sits on hosts whose routing and firewall rules will not carry traffic
originating outside the mesh to the application at all. The application and the
reverse proxy in front of it are not what keeps anybody out, and were never
intended to be. The packet does not arrive. The owner controls the hosts, the
routing and the firewall rules, so this is a posture that can be guaranteed
rather than asserted — which is the whole difference between it and the thing
that was argued against.

The road-warrior WireGuard VPN is how somebody on mobile data is *on* that
network rather than an exception to it. That was confirmed on 2026-08-31 by
observation and by first-person accounts rather than inferred: volunteers run
it, and they installed it for their own reasons rather than because this
decision asked them to.

## Decision

**1. Admission is the deployment's, and it happens at L3/L4.** No application
code decides it and no setting turns it on or off. `mesh_only` is not a value of
`VOLUNTEER_ACCESS` and will not become one; a posture the application cannot
enforce should not be spelled as though it could.

**2. The application is defence in depth, and is written as though the network
were not there.** This is the operative half of the record. Nothing in this
repository may be weakened, skipped or sized down on the grounds that the
network already stops it — not the two populations of
[0012](0012-two-populations.md), not the staff flag on every administrative
write, not the append-only ledger of
[0016](0016-invariants-for-every-writer.md), not the throttles, and not the
personal fields withheld from a caller with no account. A reviewer meeting
"the network makes this unreachable" as a justification should refuse it and
cite this line.

**3. What reaches the read surface is mesh members, not the internet.** That is
a different population from the one `inventory-tng-81f7` was framed against, and
it is worth being exact about how much smaller: not much. "On the mesh" is
thousands of members by design, plus every public space carrying a mesh
connection — a large set, deliberately, and not an accountable one. So this
stops a stranger and does not stop somebody who wants the data.

**4. A device credential is attribution, never admission.** Under an
application-level gate, enrolment would have been the door. Under this posture
the network is the door, and what a device name is for instead is enumerated
where it is implemented, in
[`devices.py`](../../backend/src/inventory_tng/devices.py). The ceiling is the
part that belongs here: the owner's words for what it buys are "weakly guard
against in-network bad actors", and nothing beyond that may be claimed for it.

## Consequences

**It is a precondition, and the application cannot tell.** Nothing above is
checked in code, because nothing above *can* be checked in code — a deployment
that skipped it is indistinguishable from within, until the first request that
proves otherwise. So it is stated to whoever stands a deployment up, in
[deployment](../deployment.md#where-this-is-reachable-from), in the same shape
and for the same reason as the administrative restriction that
[0013](0013-administrator-sign-in.md) point 6 requires — a narrower boundary,
recorded the same way because it fails the same way.

**It narrows [0013](0013-administrator-sign-in.md) point 6 rather than replacing
it.** That point restricts the administrative routes and calls itself the one
place a network boundary fits without shutting out a volunteer on a phone. The
restriction stands; the reason given for it being the only one does not, for the
same correction as everything else here — the VPN is how that phone is on the
network. What is left of point 6 is a second gate in front of the administrative
surface, which is a narrower set of paths than the deployment and a different
list from it, and point 2 is what stops this record being spent to remove it.

**Being on the network is not the same as having chosen to be.** The VPN puts a
volunteer on mobile data inside the boundary only while it is running, so the
cost of this posture lands on whoever has it installed and not enabled — as an
application that does not answer, with nothing on screen to explain why. That
is the one objection in [the brief](../briefs/authorisation.md) this record
does not dispose of, and it is named rather than left to be met.

**The failure is silent, and that is the reason this is written down.** An
application that assumes it is unreachable from outside is safe only while that
stays true, and nothing announces the day it stops — a migration to a host
somebody else routes, a second environment stood up in a hurry, an ingress
exposed to answer a health check. This record exists to be held against, not to
be leaned on, which is why point 2 is phrased as a prohibition rather than a
reassurance.

**The rules themselves are not in this repository.** They are the mesh's
routing and firewall configuration, on hosts this project does not describe, so
nothing here can verify them and no test can. That is a genuine gap in what a
reader can check, and naming it is the honest form of the claim: this is a
statement about an environment, taken from the person who administers it.

**`inventory-tng-81f7` is unchanged by all of this.** A gate decides who may
ask; it does not decide what the answer contains. Whether an anonymous caller
may learn a volunteer's contact details, and whether a custody location may be
disclosed, are questions about the *payload*, and no network posture touches
them.

**Being reachable is not being identified.** The VPN carries a request to the
application; it hands nobody a code and identifies nobody. So this settles
nothing about enrolment: `inventory-tng-izbm`, which would identify a device,
and `inventory-tng-jro`, which would sign its requests, are argued on their own
terms and neither moved because of this.

**The code already carried this correction; what it lacked was somewhere
durable to point at.** Several modules and both architecture documents had been
citing an issue id for "the network does admission", which is a reference that
answers nothing once the issue is closed. That is what a record is for, and it
is why this one is worth its number despite deciding something no code enforces.
