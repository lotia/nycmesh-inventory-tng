/**
 * The providers a screen needs to be itself, for tests that render one.
 *
 * Two: the batch in hand, and who is signed in. A screen rendered without the
 * second draws no administrative controls -- which is the safe answer, but it
 * is also the answer for a volunteer, so a test asserting "no Edit button"
 * would pass for the wrong reason. Rendering through this makes the session
 * something a test states rather than something it omits.
 */
import { fireEvent, render, screen, type within } from "@testing-library/react";
import type { ReactNode } from "react";
import { expect, vi } from "vitest";
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

/** One request this app made that was not a read. */
export interface Write {
  path: string;
  method: string;
  body: unknown;
}

/**
 * Every write the app made, as the path, the method and the body it sent.
 *
 * Here rather than in each administrative test file: what an administrative
 * screen does is send one write, and asserting that means the same three
 * things every time. Reads are left out because every screen makes several
 * before it can draw anything, which is what `callsTo` above is for.
 *
 * `method` narrows further, for a screen that has more than one: correcting a
 * row is a PATCH and adding one is a POST, and a test about either has to be
 * able to say there were none of the other.
 */
export function writes(method?: string): Write[] {
  return (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls
    .filter(([, init]) => {
      const sent = (init as RequestInit | undefined)?.method;
      return sent !== undefined && sent !== "GET" && (method === undefined || sent === method);
    })
    .map(([path, init]) => ({
      path: String(path),
      method: String((init as RequestInit).method),
      body: JSON.parse(String((init as RequestInit).body)),
    }));
}

/**
 * The one write, asserted to be exactly one before it is read.
 *
 * The assertion is the point: a screen that sent its body twice, or sent a
 * second request nobody asked for, would otherwise pass a test that only ever
 * looked at the first.
 */
export function theWrite(method?: string): Write {
  const made = writes(method);
  expect(made).toHaveLength(1);
  return made[0];
}

/**
 * Answer a MUI select: open it, then take one of what it offers.
 *
 * TWO THINGS THAT LOOK LIKE STYLE AND ARE NOT. It opens on `mouseDown` rather
 * than on a click, which is what MUI listens for. And the option list is
 * PORTALLED out of whatever the select is inside, so it is found on `screen`
 * even when the select was found `within` a dialog -- a copy that narrows the
 * second lookup fails with a "not found" that says nothing about why.
 *
 * Both of those were written down once and relied on twenty-two times before
 * this existed, which is `inventory-tng-cf7u.1`.
 */
export async function pickOption(
  // Either `screen` or a `within(...)`, which are two types answering the same
  // query: whichever of them found the select is the one that should find it.
  where: Pick<ReturnType<typeof within>, "getByRole"> | typeof screen,
  select: RegExp | string,
  option: RegExp | string,
): Promise<void> {
  fireEvent.mouseDown(where.getByRole("combobox", { name: select }));
  fireEvent.click(await screen.findByRole("option", { name: option }));
}

export function renderScreen(ui: ReactNode) {
  return render(
    <SessionProvider>
      <CartProvider>{ui}</CartProvider>
    </SessionProvider>,
  );
}
