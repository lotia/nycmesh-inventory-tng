/**
 * Where a browser's spans go, and what they carry.
 *
 * Its own module because both halves of the split read it -- `start.ts`, which
 * nothing may pull the SDK in through, and `sdk.ts`, which is the SDK -- and
 * because a test reads it without starting anything at all.
 */

import { HEADER } from "./flag";

/**
 * Where spans go: a path on this origin, forwarded by nginx.
 *
 * Same-origin is not a convenience. The policy this app is served under says
 * `connect-src 'self'`, so a browser exporter pointed anywhere else is blocked
 * outright -- and widening the policy to admit a collector would widen it for
 * everything else this page could be made to fetch.
 */
export const ENDPOINT = "/v1/traces";

/**
 * What the exporter is built with, as plain data.
 *
 * Returned rather than applied so a test can read it. What matters about it is
 * two things a reader cannot check by looking at a constructed exporter: that
 * the URL is a path on this origin, and that the token is on the post -- the
 * ingest path refuses one without it, which is what stops the collector being
 * a write anybody can make.
 */
export function wiring(token: string) {
  return { url: ENDPOINT, headers: { [HEADER]: token } };
}
