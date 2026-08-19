/**
 * Reading one resource, with the three states a screen has to draw.
 *
 * Small on purpose. There is no data-fetching library here for the same
 * reason there is no state library (docs/architecture.md, "Frontend"): the app
 * reads a handful of endpoints and re-reads them when a search box changes.
 * Anything needing caching, retries or invalidation is a decision worth
 * recording rather than a dependency added quietly.
 */
import { useEffect, useState } from "react";
import { type ApiError, apiGet, asApiError } from "./client";

export interface Resource<T> {
  data: T | null;
  error: ApiError | null;
  /** True while a request is in flight, including a re-read over stale data. */
  loading: boolean;
}

/**
 * @param path what to read.
 * @param reload a number a caller changes to ask for the same path again --
 * after an edit it has just saved, say. It is a dependency of the effect and
 * nothing else, so it never reaches the server: a counter smuggled into the
 * query string would be a cache-buster the API has to ignore and an operator
 * has to read in the logs.
 */
export function useResource<T>(path: string, reload = 0): Resource<T> {
  const [state, setState] = useState<Resource<T>>({ data: null, error: null, loading: true });

  // `reload` is a dependency the body deliberately does not read: changing it
  // is how a caller asks for the same request again.
  // biome-ignore lint/correctness/useExhaustiveDependencies: see above
  useEffect(() => {
    const controller = new AbortController();
    // The previous answer is kept while the next one is in flight, so typing
    // in a search box does not blank the list between keystrokes.
    setState((previous) => ({ ...previous, loading: true }));
    apiGet<T>(path, controller.signal)
      .then((data) => setState({ data, error: null, loading: false }))
      .catch((error: unknown) => {
        // An abort means this effect has already been replaced by a newer one,
        // whose state must not be overwritten by the corpse of the old.
        if (controller.signal.aborted) {
          return;
        }
        setState({ data: null, error: asApiError(error), loading: false });
      });
    return () => controller.abort();
  }, [path, reload]);

  return state;
}
