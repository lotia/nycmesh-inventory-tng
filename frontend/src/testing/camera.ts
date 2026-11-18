/** A camera for the tests: it sees what is put in `state.inView`, unless
 * `read` or `torch` say otherwise. Never bundled. */
import { vi } from "vitest";
import type { Session } from "../scan/camera";
import { READ_EVERY_MS } from "../scan/Scanner";

export function fakeCamera(overrides: Partial<Session> = {}) {
  const state = { inView: [] as string[], stopped: false };
  const session: Session = {
    read: async () => state.inView,
    torch: null,
    stop: () => {
      state.stopped = true;
    },
    ...overrides,
  };
  return { state, open: async () => session };
}

/** Lets `reads` reads' time pass on a faked clock, and what they did be
 * done. */
export const pass = (reads = 1) =>
  vi.advanceTimersByTimeAsync(reads * READ_EVERY_MS);
