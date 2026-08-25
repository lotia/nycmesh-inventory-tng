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
import { cable, zipTies } from "../items/testFixtures";
import {
  ADMINISTRATOR,
  renderScreen,
  STALE_ADMINISTRATOR,
  stubSession,
  theWrite,
  VOLUNTEER,
  writes,
} from "../testHarness";
import { StaleSession } from "./StepUp";

/** The groupings an item can be added to. See CreateItem. */
const CATEGORIES = [{ id: 7, name: "Cable and connectors", parent: null }];

/** An answer of this shape, which is what every stub below hands back. */
const answering = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

/**
 * The catalogue, the groupings, and whatever a write should answer with.
 *
 * Every stub in this file goes through here so that "the item list answers with
 * `page(zipTies)`" is written once. `categories` is a thunk rather than a body
 * because two of the cases below are about *when* it answers rather than what
 * it says.
 */
function catalogue(
  session: unknown,
  write: () => Response = () => answering({}),
  categories: () => Promise<Response> | Response = () => answering(page(...CATEGORIES)),
) {
  stubSession(session, (async (path: string, init?: RequestInit) => {
    // Every unsafe method, not only PATCH: correcting an item and adding one
    // are the two halves of `edit_catalogue` this screen has, and a test that
    // stubbed only the first would let the second reach the real fetch.
    if (init?.method !== undefined && init.method !== "GET") {
      return write();
    }
    if (path.startsWith("/api/categories")) {
      return categories();
    }
    // Two rows, not one: a claim that a control belongs to the collection
    // rather than to a row cannot be made against a list of one.
    return answering(page(zipTies, cable));
  }) as unknown as typeof fetch);
}

const edit = () => screen.getByRole("button", { name: /edit zip ties reusable/i });
const add = () => screen.getByRole("button", { name: /add an item/i });

/** Open the create dialog and fill in the two fields the API insists on. */
async function newItemNamed(named: string): Promise<HTMLElement> {
  fireEvent.click(add());
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(within(dialog).getByRole("textbox", { name: /^name$/i }), {
    target: { value: named },
  });
  fireEvent.mouseDown(within(dialog).getByRole("combobox", { name: /category/i }));
  // The option list is portalled out of the dialog, so it is found on the
  // screen rather than within it.
  fireEvent.click(await screen.findByRole("option", { name: CATEGORIES[0].name }));
  return dialog;
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("what a volunteer sees", () => {
  it("is no way to change the catalogue, on a row or on the list", async () => {
    catalogue(VOLUNTEER);
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });

    expect(screen.queryByRole("button", { name: /edit/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add an item/i })).not.toBeInTheDocument();
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
    expect(theWrite("PATCH").body).toMatchObject({ name: "Zip Ties" });
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
    expect(screen.queryByRole("button", { name: /add an item/i })).not.toBeInTheDocument();
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

describe("adding an item to the catalogue", () => {
  it("is offered on the list rather than on a row", async () => {
    catalogue(ADMINISTRATOR);
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });

    // One control for the collection against a list of two, which is what
    // makes this a claim about the anchor rather than an accident of the
    // fixture: a per-row control would answer two here. Both rows do carry
    // their own Edit, which is the contrast.
    expect(screen.getAllByRole("button", { name: /add an item/i })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: /^edit /i })).toHaveLength(2);
  });

  it("adds one without leaving the app", async () => {
    catalogue(ADMINISTRATOR);
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });

    const dialog = await newItemNamed("Grounding Wire");
    fireEvent.click(within(dialog).getByRole("button", { name: /^add$/i }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(theWrite("POST").body).toEqual({
      name: "Grounding Wire",
      category: CATEGORIES[0].id,
      unit_of_measure: "each",
      minimum_stock: "0",
      reorder_quantity: "1",
    });
  });

  it("sends the unit it was told, not the one it started with", async () => {
    catalogue(ADMINISTRATOR);
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });

    const dialog = await newItemNamed("Cat6 Outdoor");
    fireEvent.mouseDown(within(dialog).getByRole("combobox", { name: /counted in/i }));
    fireEvent.click(await screen.findByRole("option", { name: "Metre" }));
    fireEvent.click(within(dialog).getByRole("button", { name: /^add$/i }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(theWrite("POST").body).toMatchObject({ unit_of_measure: "metre" });
  });

  it("will not send an item with no category, because the API cannot take one", async () => {
    catalogue(ADMINISTRATOR);
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });
    fireEvent.click(add());

    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox", { name: /^name$/i }), {
      target: { value: "Grounding Wire" },
    });

    // Asked for on the form rather than discovered as a 400 the server has to
    // explain: `Item.category` is not nullable.
    expect(within(dialog).getByRole("button", { name: /^add$/i })).toBeDisabled();
    expect(writes("POST")).toHaveLength(0);
  });

  it("says so when there is no category to join", async () => {
    catalogue(ADMINISTRATOR, undefined, () => answering(page()));
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });
    fireEvent.click(add());

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/no categories yet/i)).toBeInTheDocument();
  });

  it("does not call a category list it has not read yet an empty one", async () => {
    // See CreateItem for why the two states are told apart. Held open here
    // rather than answered instantly, because a stub that resolves at once
    // never renders the state this is about.
    let answer: (rows: unknown) => void = () => {};
    const held = new Promise<unknown>((resolve) => {
      answer = resolve;
    });
    catalogue(ADMINISTRATOR, undefined, async () => answering(await held));
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });
    fireEvent.click(add());

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByText(/no categories yet/i)).not.toBeInTheDocument();
    expect(within(dialog).getByText(/reading the categories/i)).toBeInTheDocument();

    // And once it answers, neither sentence is left behind.
    answer(page(...CATEGORIES));
    await waitFor(() =>
      expect(within(dialog).queryByText(/reading the categories/i)).not.toBeInTheDocument(),
    );
    expect(within(dialog).queryByText(/no categories yet/i)).not.toBeInTheDocument();
  });

  it("does not call an unreadable category list an empty one", async () => {
    // The two look the same on screen and mean opposite things: one says to
    // make a category first, the other says the network is down and the item
    // cannot be added at all.
    catalogue(ADMINISTRATOR, undefined, () =>
      answering({ detail: "Categories could not be read." }, 500),
    );
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });
    fireEvent.click(add());

    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText(/categories could not be read/i)).toBeInTheDocument();
    expect(within(dialog).queryByText(/no categories yet/i)).not.toBeInTheDocument();
  });

  it("offers the way back when the server wants a second look", async () => {
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

    const dialog = await newItemNamed("Grounding Wire");
    fireEvent.click(within(dialog).getByRole("button", { name: /^add$/i }));

    expect(await screen.findByRole("link", { name: /sign in again/i })).toHaveAttribute(
      "href",
      expect.stringContaining("/accounts/reauthenticate/"),
    );
    // And the dialog is still there, holding what was typed, so the way back
    // does not cost the work.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("sends the stock levels as typed, not as the defaults it opened with", async () => {
    catalogue(ADMINISTRATOR);
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });

    const dialog = await newItemNamed("Grounding Wire");
    fireEvent.change(within(dialog).getByRole("textbox", { name: /minimum stock/i }), {
      target: { value: "25" },
    });
    fireEvent.change(within(dialog).getByRole("textbox", { name: /reorder quantity/i }), {
      target: { value: "100" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /^add$/i }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(theWrite("POST").body).toMatchObject({ minimum_stock: "25", reorder_quantity: "100" });
  });

  it("says the other kind of no as a sentence, and keeps the form", async () => {
    // The body the server really sends for a duplicate name: DRF keys a
    // field's own refusal by field name and there is no `detail` anywhere in
    // it. A stub carrying one would be green over a screen saying nothing.
    catalogue(
      ADMINISTRATOR,
      () =>
        new Response(JSON.stringify({ name: ["item with this name already exists."] }), {
          status: 400,
        }),
    );
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });

    const dialog = await newItemNamed("Zip Ties Reusable");
    fireEvent.click(within(dialog).getByRole("button", { name: /^add$/i }));

    expect(await screen.findByText(/already exists/i)).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    // And it can be put away, leaving the form to be corrected rather than
    // an alert stuck above the field it is about.
    fireEvent.click(within(dialog).getByRole("button", { name: /close/i }));
    await waitFor(() => expect(screen.queryByText(/already exists/i)).not.toBeInTheDocument());
  });

  it("leaves the catalogue alone when the add is abandoned", async () => {
    catalogue(ADMINISTRATOR);
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });

    const dialog = await newItemNamed("Grounding Wire");
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(writes("POST")).toHaveLength(0);
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
    expect(theWrite("PATCH").body).toMatchObject({
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
    expect(writes("PATCH")).toHaveLength(0);
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
