/**
 * What an administrator sees that a volunteer does not, and what the server
 * has to say before either of them sees anything.
 *
 * Decision 0014 point 3 is the claim under test: the interface renders these
 * controls from the server's answer rather than guessing. So every case here
 * states a session and asserts what is drawn -- including the case that
 * matters most, a control that is *not* drawn.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { page } from "../api/testFixtures";
import { ItemList } from "../items/ItemList";
import { zipTies } from "../items/testFixtures";
import {
  ADMINISTRATOR,
  renderScreen,
  STALE_ADMINISTRATOR,
  stubSession,
  VOLUNTEER,
} from "../testHarness";
import { StaleSession } from "./StepUp";

/** The catalogue, plus whatever the write should answer with. */
function catalogue(
  session: unknown,
  write: () => Response = () => new Response("{}", { status: 200 }),
) {
  stubSession(session, (async (_path: string, init?: RequestInit) => {
    if (init?.method === "PATCH") {
      return write();
    }
    return new Response(JSON.stringify(page(zipTies)), { status: 200 });
  }) as unknown as typeof fetch);
}

const edit = () => screen.getByRole("button", { name: /edit zip ties reusable/i });

/** Every write the app made, as the bodies it sent. */
function written(): unknown[] {
  return (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls
    .filter(([, init]) => (init as RequestInit | undefined)?.method === "PATCH")
    .map(([, init]) => JSON.parse(String((init as RequestInit).body)));
}

/** The one write, asserted to be exactly one before it is read. */
function theWrite(): unknown {
  const writes = written();
  expect(writes).toHaveLength(1);
  return writes[0];
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("what a volunteer sees", () => {
  it("is no editing control at all", async () => {
    catalogue(VOLUNTEER);
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });

    expect(screen.queryByRole("button", { name: /edit/i })).not.toBeInTheDocument();
  });
});

describe("what an administrator sees", () => {
  it("is the same item, with a way to correct it", async () => {
    catalogue(ADMINISTRATOR);
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });

    expect(edit()).toBeInTheDocument();
  });

  it("edits it in place, without leaving the app", async () => {
    catalogue(ADMINISTRATOR);
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });
    fireEvent.click(edit());

    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox", { name: /^name$/i }), {
      target: { value: "Zip Ties" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(theWrite()).toMatchObject({ name: "Zip Ties" });
  });

  it("retires an item rather than deleting it", async () => {
    catalogue(ADMINISTRATOR);
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });
    fireEvent.click(edit());

    const dialog = await screen.findByRole("dialog");
    // The word on the control says what happens: it stops being offered, and
    // stays in the ledger.
    expect(within(dialog).getByRole("switch", { name: /offered in the pick-list/i })).toBeChecked();
  });
});

describe("a session the server wants a second look at", () => {
  it("draws no control it cannot use", async () => {
    // The capability is what the caller may do *now*, and a stale session may
    // not edit -- so the control goes, rather than failing when pressed.
    catalogue(STALE_ADMINISTRATOR);
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });

    expect(screen.queryByRole("button", { name: /edit/i })).not.toBeInTheDocument();
  });

  it("offers the way back before anything is pressed", async () => {
    // Without this a stale session is a dead end: every administrative control
    // is gone, so the refusal that would have offered the prompt never
    // happens. `recently_authenticated` is what says this is the entitled
    // person rather than a volunteer.
    catalogue(STALE_ADMINISTRATOR);
    renderScreen(
      <>
        <StaleSession />
        <ItemList />
      </>,
    );
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });

    expect(await screen.findByRole("link", { name: /sign in again/i })).toHaveAttribute(
      "href",
      expect.stringContaining("/accounts/reauthenticate/"),
    );
  });

  it("says nothing at all to a volunteer, or to an administrator who just signed in", async () => {
    catalogue(VOLUNTEER);
    const volunteer = renderScreen(<StaleSession />);
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: /sign in again/i })).not.toBeInTheDocument(),
    );
    volunteer.unmount();

    catalogue(ADMINISTRATOR);
    renderScreen(<StaleSession />);
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: /sign in again/i })).not.toBeInTheDocument(),
    );
  });

  it("offers a way back in when the server refuses mid-edit", async () => {
    // The session can go stale between drawing the control and pressing it,
    // which is the case a hidden control cannot cover.
    catalogue(
      ADMINISTRATOR,
      () =>
        new Response(
          JSON.stringify({
            detail: "Sign in again to make this change.",
            code: "reauthentication_required",
          }),
          { status: 403 },
        ),
    );
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });
    fireEvent.click(edit());
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: /^save$/i }),
    );

    expect(await screen.findByText(/sign in again to make this change/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /sign in again/i })).toHaveAttribute(
      "href",
      expect.stringContaining("/accounts/reauthenticate/"),
    );
  });

  it("says the other kind of no as a sentence, not as a prompt", async () => {
    catalogue(
      ADMINISTRATOR,
      () =>
        new Response(
          JSON.stringify({
            detail: "This operation is reserved for administrators.",
            code: "forbidden",
          }),
          { status: 403 },
        ),
    );
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });
    fireEvent.click(edit());
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: /^save$/i }),
    );

    expect(await screen.findByText(/reserved for administrators/i)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /sign in again/i })).not.toBeInTheDocument();
  });
});

describe("the rest of the edit form", () => {
  it("sends the stock levels as typed, not as numbers it guessed at", async () => {
    catalogue(ADMINISTRATOR);
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });
    fireEvent.click(edit());

    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox", { name: /minimum stock/i }), {
      target: { value: "25" },
    });
    fireEvent.change(within(dialog).getByRole("textbox", { name: /reorder quantity/i }), {
      target: { value: "100" },
    });
    fireEvent.click(within(dialog).getByRole("switch", { name: /offered in the pick-list/i }));
    fireEvent.click(within(dialog).getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(theWrite()).toMatchObject({
      minimum_stock: "25",
      reorder_quantity: "100",
      active: false,
    });
  });

  it("leaves the item alone when the edit is abandoned", async () => {
    catalogue(ADMINISTRATOR);
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });
    fireEvent.click(edit());

    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(written()).toHaveLength(0);
  });

  it("draws nothing at all when the session cannot be read", async () => {
    // The network is what fails in a basement. Nobody is the safe answer: the
    // server would refuse an administrative write from an unknown caller.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => {
        if (path.startsWith("/api/me")) {
          throw new TypeError("Failed to fetch");
        }
        return new Response(JSON.stringify(page(zipTies)), { status: 200 });
      }),
    );
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });

    expect(screen.queryByRole("button", { name: /edit/i })).not.toBeInTheDocument();
  });
});
