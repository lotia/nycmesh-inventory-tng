/**
 * The one place this app talks to the API.
 *
 * Paths are relative, always. The browser reaches the API on the origin it
 * loaded the app from, which is what lets one frontend image run in every
 * environment -- see docs/architecture.md, "Shape". A base URL read from the
 * environment would compile an environment into the bundle and break that.
 */

import { HEADER, asked as tracing } from "../telemetry/flag";
import { failed } from "../telemetry/report";
import { csrfToken } from "./csrf";

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
    /**
     * Whether nothing was reached, as opposed to something answering badly.
     *
     * Recorded where the failure happened rather than read back off a status
     * of 0, because that status also lands on anything `asApiError` had to
     * dress up. A bug in this app throwing on the save path is not a signal
     * problem, and treating it as one hands a batch to a queue that will
     * replay it into the same bug for the rest of the device's life.
     */
    readonly offline: boolean = false,
  ) {
    super(message);
    this.name = "ApiError";
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
 * One request, and the three ways it can end: an answer, a refusal, or
 * nothing at the other end.
 *
 * Written once because reading and writing differ only in what they send.
 * Everything after the send -- what an unreachable service looks like, that
 * an abort stays an abort, that a refusal keeps its parsed body -- is the
 * same, and two copies of it are two places for one of them to drift.
 */
/**
 * The headers this request carries beyond the caller's own.
 *
 * One header, and only while an administrator has asked for this device to be
 * recorded: the signed token from `telemetry/flag.ts`. The backend records a
 * request carrying it in full whatever its sampling rate says, so a volunteer
 * meeting a failure produces the trace that explains it.
 */
function asked(init: RequestInit): RequestInit {
  const token = tracing();
  if (token === null) {
    return init;
  }
  return { ...init, headers: { ...init.headers, [HEADER]: token } };
}

async function request<T>(path: string, init: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, asked(init));
  } catch (cause) {
    // An abort is the caller's own doing -- a search moving on, a screen
    // closing -- so it is re-thrown rather than dressed up as a failure.
    if (isAbort(cause)) {
      throw cause;
    }
    throw new ApiError(0, UNREACHABLE, null, true);
  }
  const body = await parse(response);
  if (!response.ok) {
    const refused = new ApiError(response.status, detailOf(body, response.status), body);
    // A 5xx and nothing below it. A 4xx is the API refusing something and the
    // screen renders what it said -- reporting those would be reporting the
    // ordinary. A 5xx is this system failing at a volunteer, which is the
    // thing nobody would otherwise hear about. `path` and not the query
    // string: what was asked for is in the URL and is not ours to send.
    if (response.status >= 500) {
      failed(`${response.status} from ${path.split("?")[0]}`, "api", "server-error");
    }
    throw refused;
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

/**
 * Whatever was thrown, as the error a screen can render.
 *
 * Anything that is not already one of ours is *not* marked offline: it is a
 * throw from this app's own code, and the one caller that acts on `offline`
 * would otherwise queue the batch and replay a programming error.
 */
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
