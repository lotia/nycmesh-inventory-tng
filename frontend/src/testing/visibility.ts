/** The page shown or hidden, as the tests say: document.visibilityState
 * stubbed to read what they set. Never bundled. */
import { vi } from "vitest";

let visibility: DocumentVisibilityState = "visible";

/** Makes document.visibilityState read what the tests set, shown to begin
 * with. Call it in beforeEach; vi.restoreAllMocks undoes it. */
export function stubVisibility() {
  visibility = "visible";
  vi.spyOn(document, "visibilityState", "get").mockImplementation(
    () => visibility,
  );
}

/** The page shown or hidden, without telling it (no visibilitychange). */
export function setVisibility(state: DocumentVisibilityState) {
  visibility = state;
}

/** The page shown or hidden, as the browser says it. */
export function show(state: DocumentVisibilityState) {
  setVisibility(state);
  document.dispatchEvent(new Event("visibilitychange"));
}
