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
