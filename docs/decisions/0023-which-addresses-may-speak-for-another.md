# 0023 — Which addresses may speak for another

**Status:** accepted

## Context

`X-Forwarded-For` is a list a caller starts and every proxy appends to. Nothing
in it says which entries were appended and which were typed, so any rule that
reads it is really reading a claim about the deployment, and the only question
is whose claim.

DRF's throttles answer with a count: the client is `NUM_PROXIES` entries back
from the right. That works while a request really did cross that many proxies,
and DRF clamps when it did not — `addrs[-min(num_proxies, len(addrs))]` — so a
header written entirely by the caller is handed back as the caller. With the
shipped `NUM_PROXIES=2`:

```
X-Forwarded-For: 10.69.0.1, 9.9.9.9   ->  10.69.0.1
```

A guard that refuses a header with fewer entries than the hop count does not
close this, because entries cost nothing:

```
X-Forwarded-For: 10.69.0.1            ->  ''           (refused)
X-Forwarded-For: 10.69.0.1,           ->  10.69.0.1    (one comma)
```

Both measurements were taken against a real checkout, and
`backend/src/inventory/tests/test_forwarded.py` holds the second so that
whoever moves this line is told.

The case that matters is not the clever one. It is the ordinary one: a request
that reaches this application without crossing the proxies it was assumed to —
a misrouted ingress, a probe, anything else on the pod network — and a count is
wrong about that request in the dangerous direction, at exactly the moment the
deployment is already wrong.

## Decision

**A deployment names the addresses it will believe a forwarded header from, and
`REMOTE_ADDR` is checked against that list before any of the header is read.**
`TRUSTED_PROXIES` is that list, as addresses or CIDR blocks;
`inventory_tng.forwarded.client_address` is the reading, and it counts nothing.
The peer must be one of ours, and then the header is walked from the right,
discarding entries this deployment put there itself and stopping at the first
it did not.

That reading has no forgeable arrangement, because a proxy appends *after* what
it received: whatever a caller writes, their own address is appended to the
right of it, and the walk stops there or sooner. What it cannot survive is a
list that trusts the wrong thing — an address on it may claim to speak for
anybody, which is what trusting an address means — so getting the list too wide
is the failure, and there is no partial credit for it.

**The default is the empty list, and it is safe rather than merely current.**
Believing no header means every caller behind a proxy looks like the proxy: the
answer is under-attributed, never over-attributed. A deployment that configures
nothing therefore cannot be lied to.

**The rate limits deliberately keep counting hops.** `NUM_PROXIES` still reads
the throttles' client, and the two readings are allowed to differ because what
they are worth differs. Believing a forger in a throttle costs one bucket;
believing one in an admission decision costs the roster. And the failure of
routing the throttles through the sound reading is not hypothetical: with the
empty default, every volunteer behind the ingress would share one `20/min`
bucket, so a security fix for a decision nothing makes yet would take a packing
night down. `.env.sample` says this beside both variables so the pair cannot
drift apart quietly.

## Consequences

**`Device.enrolled_from` reads `TRUSTED_PROXIES`, and nothing admits by
address.** The read is through `client_address`, so a burst of minted devices is
findable by the address that asked for them — attribution, and not the thing
this paragraph was written expecting. Admission by address is now foreclosed
outright: [0030](0030-the-network-is-the-access-control.md) point 1 puts it at
the network, where no application code decides it.

*Corrected 2026-09-01.* This said nothing read the value, that every endpoint
asked for a session, and that `inventory-tng-gnhl` was the issue that would
admit by address. The first two were true when written and have been overtaken;
the third was a forecast, and `gnhl` shipped `VOLUNTEER_ACCESS=open` without
admitting anybody by address. `inventory-tng-xwx0` is where each was corrected,
and the decision above is untouched.

**The value is the cluster's, not the application's**, so it is in the chart
(`django.trustedProxies`) and in
[deployment](../deployment.md#which-addresses-may-speak-for-another) with the
prose about which way it is dangerous, rather than being a default anybody
could ship.

**A malformed entry stops the process at boot.** It is parsed during settings
import, beside the telemetry settings and for the same reason: a list nobody
can read is better found by a pod that will not start than by the first request
that needed it.

**Two readings of one header now exist in this repository**, which is a cost,
and the way out named here cannot arrive: it said the debt is paid down "when
something makes an admission decision", and decision 0030 means nothing in the
application ever makes one.

What retires it is a decision about the throttles, and the trade above no longer
prices it. That trade costs a forged header at one bucket on the premise
corrected above — that nothing anonymous reaches a throttle — and an open
posture removes the premise. `inventory-tng-9bzn` owns the question and the
arithmetic; it is not settled here.
