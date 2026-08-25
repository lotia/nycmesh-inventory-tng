// Adds jest-dom matchers (toBeInTheDocument, toHaveTextContent, ...) to
// Vitest's expect, and unmounts React trees between tests.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

/**
 * Put jsdom's Storage where this suite expects to find it.
 *
 * Node 26 defines `localStorage` and `sessionStorage` on `globalThis` itself.
 * They are its own experimental pair, and they answer `undefined` unless the
 * process was started with `--localstorage-file`. Vitest assembles the test
 * global by copying jsdom's window across, and it passes over any name the
 * global already carries unless that name is one it knows to override --
 * neither of these two is on its list. So jsdom's working Storage is never
 * installed, node's empty one is what `window.localStorage` answers with, and
 * around 250 of this suite's tests fail on `.clear()` of `undefined`.
 *
 * Node 24, which `mise.toml` pins, defines neither name, so the copy happens
 * and nothing is wrong there. That is the whole of the difference between the
 * two: it is not the opaque-origin fault it looks like, and jsdom is handed a
 * real URL on both -- `test-setup.test.ts` pins that so the wrong lead is not
 * followed twice.
 *
 * jsdom's own window is reachable as `globalThis.jsdom`, which vitest sets for
 * exactly this kind of reach-through, so the repair is to install what the copy
 * would have installed. inventory-tng-s6w3 carries the measurements.
 */
function restoreStorageFromJsdom(): void {
  const globals = globalThis as unknown as Record<string, unknown>;
  const dom = globals.jsdom as { window: Record<string, unknown> } | undefined;
  if (dom === undefined) {
    return;
  }
  for (const name of ["localStorage", "sessionStorage"]) {
    if (globals[name] === undefined && dom.window[name] !== undefined) {
      Object.defineProperty(globalThis, name, {
        value: dom.window[name],
        configurable: true,
        writable: true,
      });
    }
  }
}

restoreStorageFromJsdom();

afterEach(cleanup);
