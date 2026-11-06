/** A camera for the tests: it sees what is put in `state.inView`, unless
 * `read` or `torch` say otherwise. Never bundled. */
import type { Session } from "./camera";

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
