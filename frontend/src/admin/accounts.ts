/**
 * Where the sign-in pages are, and how to be sent back afterwards.
 *
 * Django owns every one of these -- allauth is mounted at `/accounts/`, and
 * decision 0013 says why that is one dependency rather than a form of ours.
 * What this module owns is only the composition: the path, plus where to
 * return to, spelled the same way each time.
 *
 * Here rather than beside either caller because there are two of them and they
 * want different pages for opposite reasons. `StepUp` sends an administrator
 * to re-authenticate in the middle of something; the way in sends somebody who
 * has not signed in at all. A second copy of this would be a second answer to
 * "where does the app come back to", and the one thing both must agree on is
 * that it comes back at all -- a sign-in that lands somebody on a Django page
 * with no memory of why is the interruption decision 0014 point 5 was trying
 * to keep small.
 */

/** The page this app is on, as allauth's `next` wants it. */
function here(): string {
  return encodeURIComponent(window.location.pathname + window.location.search);
}

/**
 * One of allauth's pages, told where to come back to.
 *
 * @param page the path under `/accounts/`, with its trailing slash -- Django's
 * `APPEND_SLASH` would otherwise answer the redirect rather than the page, and
 * a POST that met that would lose its body.
 */
export function accountsPage(page: string): string {
  return `/accounts/${page}?next=${here()}`;
}
