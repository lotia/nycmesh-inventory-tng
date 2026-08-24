/**
 * The CSRF token this origin's cookie carries.
 *
 * Its own module because two callers need it and one of them cannot import the
 * other: `api/client.ts` is every deliberate write, and `telemetry/report.ts`
 * is the failure report -- which `client.ts` itself calls, so reaching back
 * into it would be a cycle.
 *
 * Where the cookie comes from is the endpoint index: fetching `/api` is what
 * hands a browser one. See `ApiRootView` in backend/src/inventory/views.py.
 * Read on each write rather than cached, because it is rotated on sign-in.
 */

export function csrfToken(): string {
  // `document.cookie` rather than the Cookie Store API: that one is
  // asynchronous, so reading it would make every write a two-step, and it is
  // absent from Safari and from the jsdom the unit tests run in. There is one
  // cookie to read and it is not a hot path.
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}
