import { type Inputs, useEffect, useRef } from "preact/hooks";

/** How often an open page checks whether to ask whose name it is, and
 * whether its catalogue is stale. */
export const CHECK_EVERY_MS = 60_000;

/** Run check every CHECK_EVERY_MS and whenever the page is shown again,
 * never while it is hidden. The timer and listener are set up again when
 * deps change; the check run is always the latest one rendered. */
export function useEveryMinuteAndWhenShown(check: () => void, deps: Inputs) {
  const latest = useRef(check);
  latest.current = check;
  useEffect(() => {
    const run = () => {
      if (document.visibilityState === "visible") {
        latest.current();
      }
    };
    const timer = setInterval(run, CHECK_EVERY_MS);
    document.addEventListener("visibilitychange", run);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", run);
    };
  }, deps);
}
