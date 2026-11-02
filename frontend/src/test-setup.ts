import { cleanup } from "@testing-library/preact";
import { afterEach, vi } from "vitest";

// Vitest's globals are off, so Testing Library cannot clean up by itself:
// without this, each test's page stays in the document for the next.
afterEach(() => {
  cleanup();
  // A test that opens a page at an address must not leave it to the next.
  window.history.replaceState(null, "", "/");
  // Nor its cart, or its outbox.
  localStorage.clear();
  // A test that stopped the clock must not leave it stopped.
  vi.useRealTimers();
});
