/**
 * The providers a screen needs to be itself, for tests that render one.
 *
 * Two: the batch in hand, and who is signed in. A screen rendered without the
 * second draws no administrative controls -- which is the safe answer, but it
 * is also the answer for a volunteer, so a test asserting "no Edit button"
 * would pass for the wrong reason. Rendering through this makes the session
 * something a test states rather than something it omits.
 */
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { vi } from "vitest";
import { SessionProvider } from "./admin/SessionProvider";
import { CartProvider } from "./cart/CartProvider";

/** `GET /api/me` for somebody who has not signed in: the ordinary case. */
export const VOLUNTEER = {
  authenticated: false,
  username: null,
  administrator: false,
  recently_authenticated: false,
  capabilities: {
    append_stock: true,
    add_volunteer: true,
    edit_catalogue: false,
    print_label: false,
    revoke_label: false,
    merge_volunteers: false,
  },
};

/** An administrator who signed in a moment ago. */
export const ADMINISTRATOR = {
  ...VOLUNTEER,
  authenticated: true,
  username: "editor",
  administrator: true,
  recently_authenticated: true,
  capabilities: Object.fromEntries(Object.keys(VOLUNTEER.capabilities).map((name) => [name, true])),
};

/** An administrator whose session is no longer recent enough to change things. */
export const STALE_ADMINISTRATOR = {
  ...ADMINISTRATOR,
  recently_authenticated: false,
  // Exactly a volunteer's: appending is what a stale session keeps, which is
  // decision 0014 point 5's deliberate narrowness.
  capabilities: VOLUNTEER.capabilities,
};

/**
 * Answer `/api/me` with this session, and everything else with `answer`.
 *
 * Wraps rather than replaces whatever the caller was already stubbing, so a
 * test that cares about one endpoint does not have to know about this one.
 */
export function stubSession(session: unknown, answer: typeof fetch): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      if (typeof path === "string" && path.startsWith("/api/me")) {
        return new Response(JSON.stringify(session), { status: 200 });
      }
      return answer(path, init);
    }),
  );
}

/**
 * The calls a test cares about, without the session read this harness adds.
 *
 * Every screen now asks who is signed in on mount, so a test asserting "no
 * label was resolved" has to say which calls it meant.
 */
export function callsTo(prefix: string): unknown[] {
  return (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.filter(
    ([path]) => typeof path === "string" && path.startsWith(prefix),
  );
}

export function renderScreen(ui: ReactNode) {
  return render(
    <SessionProvider>
      <CartProvider>{ui}</CartProvider>
    </SessionProvider>,
  );
}
