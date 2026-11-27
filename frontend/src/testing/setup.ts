import { act, cleanup } from "@testing-library/preact";
import { afterEach, vi } from "vitest";
import { messages as english } from "../locales/en.po";
import { speak } from "../shared/i18n";
import { takeSeparatorFrom } from "../shared/quantity";
import { stopIgnoringTaps } from "../shared/settling";

// Vitest's globals are off, so Testing Library cannot clean up by itself:
// without this, each test's page stays in the document for the next.
afterEach(() => {
  // In act, so each page's effects are cleaned up in its own test.
  act(cleanup);
  // A test that opens a page at an address must not leave it to the next.
  window.history.replaceState(null, "", "/");
  // Nor its cart, or its outbox.
  localStorage.clear();
  // A test that stopped the clock must not leave it stopped.
  vi.useRealTimers();
  // A test that spoke another language must not leave it spoken.
  speak("en", english);
  // Nor its page's decimal separator.
  takeSeparatorFrom(document.createElement("html"));
  // Nor its page's taps ignored (shared/settling.ts, ignoreTaps).
  stopIgnoringTaps(document);
});
