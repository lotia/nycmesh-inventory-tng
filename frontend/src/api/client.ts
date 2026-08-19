/**
 * The one place this app talks to the API.
 *
 * Paths are relative, always. The browser reaches the API on the origin it
 * loaded the app from, which is what lets one frontend image run in every
 * environment -- see docs/architecture.md, "Shape". A base URL read from the
 * environment would compile an environment into the bundle and break that.
 */

/**
 * A request the API answered, and refused.
 *
 * Carries the status because a client branches on it -- a 403 is a sign-in
 * prompt, a 404 is an empty state -- and the parsed body because the API
 * refuses in a typed body rather than in prose (`DetailSerializer` and the
 * batch error shapes in `backend/src/inventory/serializers.py`).
 *
 * A request that never reached the API is one of these too, with a status of
 * 0: from a volunteer's point of view the basement and the 500 are the same
 * event, and both have to render.
 */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly body: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** Whether nothing was reached, as opposed to something answering badly. */
  get offline(): boolean {
    return this.status === 0;
  }
}

const UNREACHABLE =
  "The inventory service could not be reached. Check the connection and try again.";

/** The sentence to show, preferring what the API said over what we would guess. */
function detailOf(body: unknown, status: number): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") {
      return detail;
    }
  }
  return `The inventory service answered ${status}.`;
}

async function parse(response: Response): Promise<unknown> {
  // A body that is not JSON is not a failure to report on its own: an empty
  // 204 and an HTML error page from something in front of Django both land
  // here, and the status is what the caller acts on either way.
  try {
    return await response.json();
  } catch {
    return null;
  }
}

/**
 * The CSRF token Django expects back on every write.
 *
 * Session authentication enforces CSRF, and a single-page app never renders a
 * Django template, so the cookie is set by fetching the API index -- see
 * `ApiRootView` in backend/src/inventory/views.py. Read from the cookie on
 * each write rather than cached, because it is rotated on sign-in.
 */
function csrfToken(): string {
  // `document.cookie` rather than the Cookie Store API: that one is
  // asynchronous, so reading it would make every write a two-step, and it is
  // absent from Safari and from the jsdom the unit tests run in. There is one
  // cookie to read and it is not a hot path.
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}

/**
 * One request, and the three ways it can end: an answer, a refusal, or
 * nothing at the other end.
 *
 * Written once because reading and writing differ only in what they send.
 * Everything after the send -- what an unreachable service looks like, that
 * an abort stays an abort, that a refusal keeps its parsed body -- is the
 * same, and two copies of it are two places for one of them to drift.
 */
async function request<T>(path: string, init: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch (cause) {
    // An abort is the caller's own doing -- a search moving on, a screen
    // closing -- so it is re-thrown rather than dressed up as a failure.
    if (isAbort(cause)) {
      throw cause;
    }
    throw new ApiError(0, UNREACHABLE);
  }
  const body = await parse(response);
  if (!response.ok) {
    throw new ApiError(response.status, detailOf(body, response.status), body);
  }
  return body as T;
}

/** Read one resource. Rejects with an `ApiError` for anything but a 2xx. */
export function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { headers: { Accept: "application/json" }, signal });
}

/**
 * Write one resource. Rejects with an `ApiError` for anything but a 2xx, and
 * the refused body is on the error -- a 409 from the volunteer endpoint is a
 * thing to render, not a thing to give up on.
 *
 * JSON only: the API takes nothing else (`DEFAULT_PARSER_CLASSES` in
 * backend/src/inventory_tng/settings.py), because a form encoding has no null
 * and cannot carry an array of objects.
 */
export function apiPost<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  return written<T>("POST", path, body, signal);
}

/**
 * Change one resource.
 *
 * PATCH rather than PUT because the API offers no PUT on a detail endpoint: a
 * row is corrected, never replaced, since a replacement omitting `active`,
 * `merged_into` or `revoked` would withdraw it without saying so. See
 * DetailView in backend/src/inventory/views.py.
 */
export function apiPatch<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  return written<T>("PATCH", path, body, signal);
}

function written<T>(method: string, path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  return request<T>(path, {
    method,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken(),
    },
    body: JSON.stringify(body),
    signal,
  });
}

/**
 * Whether this is the caller's own cancellation rather than a failure.
 *
 * Stated here because this module is what decides an abort stays an abort:
 * `apiGet`/`apiPost` re-throw it untouched so a caller that gave up is never
 * told something went wrong.
 */
export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/**
 * A refused body, if it is the typed shape this caller expects.
 *
 * The API refuses in typed bodies -- a 409 naming a merged volunteer, a 400
 * listing a batch's bad lines -- and each caller has to decide whether what
 * came back is the one it can render. The narrowing of `ApiError.body` belongs
 * here rather than in each screen.
 */
export function refusalBody<T>(
  error: ApiError,
  looksRight: (body: Partial<T>) => boolean,
): T | null {
  if (typeof error.body !== "object" || error.body === null) {
    return null;
  }
  const body = error.body as Partial<T>;
  return looksRight(body) ? (body as T) : null;
}

/** Whatever was thrown, as the error a screen can render. */
export function asApiError(error: unknown): ApiError {
  return error instanceof ApiError ? error : new ApiError(0, String(error));
}

/**
 * A collection, narrowed by what somebody typed into a search box.
 *
 * One definition because every list screen needs it and they must agree on
 * what a blank or space-only query means: nothing typed is the whole first
 * page, not a search for "".
 */
export function searchPath(collection: string, search: string): string {
  const trimmed = search.trim();
  return trimmed === "" ? collection : `${collection}?search=${encodeURIComponent(trimmed)}`;
}
