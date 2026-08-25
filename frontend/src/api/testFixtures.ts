/**
 * The page envelope every list endpoint answers with, for tests that stub one.
 *
 * Here rather than beside one screen's fixtures because it is the API's shape,
 * not any one collection's -- see `Page` in ./types.
 */
import type { Page } from "./types";

export function page<T>(...rows: T[]): Page<T> {
  return { count: rows.length, next: null, previous: null, results: rows };
}

/**
 * One answer, as this API sends one.
 *
 * Here for the reason `page` is: JSON in the body and a status, which is what
 * every stub in this repository hands back, and 200 by default because that is
 * what all but a handful of them want. A refusal says its own status, which is
 * the only part worth reading at a call site.
 */
export function answering(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}
