/**
 * The two small vocabularies, made and corrected where an item and a batch
 * name one.
 *
 * Neither a location nor a category is a screen anybody goes to, so there is no
 * screen to test: what these assert is that the controls are on the two pickers
 * where somebody discovers the row they want is missing, that they are drawn
 * from the capability and from nothing else, and that what is sent is what the
 * API takes. Decision 0014 point 1 is the claim; `administrator.test.tsx` is
 * the same claim about an item.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { page } from "../api/testFixtures";
import type { Category, Location } from "../api/types";
import { LocationPicker } from "../batch/LocationPicker";
import { ItemList } from "../items/ItemList";
import { zipTies } from "../items/testFixtures";
import {
  ADMINISTRATOR,
  renderScreen,
  stubSession,
  theWrite,
  VOLUNTEER,
  writes,
} from "../testHarness";

const CABLES: Category = { id: 7, name: "Cable and connectors", parent: null };
/** Nested, so the form has a parent to keep as well as a name to change. */
const FITTINGS: Category = { id: 9, name: "Fittings", parent: 7 };
const WAREHOUSE: Location = {
  id: 3,
  name: "131 Broome",
  kind: "warehouse",
  parent: null,
  held_by: null,
  active: true,
};
const SHELF: Location = { ...WAREHOUSE, id: 4, name: "Shelf 1", kind: "shelf", parent: 3 };

const answering = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

/**
 * Every collection these screens read, and whatever a write should answer with.
 *
 * One stub for both halves, because both screens are rendered against it and a
 * second would be a second statement of what the warehouse holds. `places` is a
 * thunk rather than a list, the way `catalogue()` in administrator.test.tsx
 * takes its groupings: half of what these assert is that the picker uses the
 * row that was just made, and a stub answering with the two it started with
 * cannot show that.
 */
function warehouse(
  session: unknown,
  write: () => Response = () => answering(WAREHOUSE, 201),
  places: () => Location[] = () => [WAREHOUSE, SHELF],
) {
  stubSession(session, (async (path: string, init?: RequestInit) => {
    if (init?.method !== undefined && init.method !== "GET") {
      return write();
    }
    if (path.startsWith("/api/categories")) {
      return answering(page(CABLES, FITTINGS));
    }
    if (path.startsWith("/api/locations")) {
      return answering(page(...places()));
    }
    return answering(page(zipTies));
  }) as unknown as typeof fetch);
}

/** Open the item dialog, which is where a category is made. */
async function addingAnItem(): Promise<HTMLElement> {
  renderScreen(<ItemList />);
  await screen.findByRole("heading", { name: "Zip Ties Reusable" });
  fireEvent.click(screen.getByRole("button", { name: /add an item/i }));
  return screen.findByRole("dialog");
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a category, made where an item is put in one", () => {
  it("is not offered to a volunteer, because the item dialog is not either", async () => {
    warehouse(VOLUNTEER);
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });

    expect(screen.queryByRole("button", { name: /add an item/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /new category/i })).not.toBeInTheDocument();
  });

  it("opens under the select without taking the item away", async () => {
    warehouse(ADMINISTRATOR, () => answering(FITTINGS, 201));
    const dialog = await addingAnItem();
    fireEvent.change(within(dialog).getByRole("textbox", { name: /^name$/i }), {
      target: { value: "Grounding Wire" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /new category/i }));

    // Inside the item's own dialog rather than over it, so what has been typed
    // is still there when the grouping is settled. Decision 0025 point 6.
    const panel = await within(dialog).findByRole("region", { name: /add a category/i });
    expect(panel).toBeInTheDocument();
    expect(within(dialog).getByRole("textbox", { name: /^name$/i })).toHaveValue("Grounding Wire");
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });

  it("posts the grouping and then chooses it", async () => {
    warehouse(ADMINISTRATOR, () => answering(FITTINGS, 201));
    const dialog = await addingAnItem();
    fireEvent.click(within(dialog).getByRole("button", { name: /new category/i }));

    const panel = await within(dialog).findByRole("region", { name: /add a category/i });
    fireEvent.change(within(panel).getByRole("textbox", { name: /category name/i }), {
      target: { value: "Fittings" },
    });
    fireEvent.click(within(panel).getByRole("button", { name: /save category/i }));

    await waitFor(() =>
      expect(
        within(dialog).queryByRole("region", { name: /add a category/i }),
      ).not.toBeInTheDocument(),
    );
    expect(theWrite()).toEqual({
      path: "/api/categories",
      method: "POST",
      body: { name: "Fittings", parent: null },
    });
    // Chosen on the way back: making one is what somebody does when it is the
    // one they wanted.
    expect(within(dialog).getByRole("combobox", { name: /category/i })).toHaveTextContent(
      "Fittings",
    );
  });

  it("nests it where it was told to", async () => {
    warehouse(ADMINISTRATOR, () => answering(FITTINGS, 201));
    const dialog = await addingAnItem();
    fireEvent.click(within(dialog).getByRole("button", { name: /new category/i }));

    const panel = await within(dialog).findByRole("region", { name: /add a category/i });
    fireEvent.change(within(panel).getByRole("textbox", { name: /category name/i }), {
      target: { value: "Connectors" },
    });
    fireEvent.mouseDown(within(panel).getByRole("combobox", { name: /inside/i }));
    fireEvent.click(await screen.findByRole("option", { name: CABLES.name }));
    fireEvent.click(within(panel).getByRole("button", { name: /save category/i }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(theWrite().body).toEqual({ name: "Connectors", parent: CABLES.id });
  });

  it("corrects the one that is chosen, from the same place", async () => {
    warehouse(ADMINISTRATOR, () => answering(FITTINGS));
    const dialog = await addingAnItem();
    fireEvent.mouseDown(within(dialog).getByRole("combobox", { name: /category/i }));
    fireEvent.click(await screen.findByRole("option", { name: FITTINGS.name }));
    fireEvent.click(within(dialog).getByRole("button", { name: `Rename ${FITTINGS.name}` }));

    const panel = await within(dialog).findByRole("region", { name: /rename/i });
    // It arrives holding what the row says, so a rename is a rename rather
    // than a re-typing -- and the parent it already had is kept, rather than
    // a save meant to change a name moving the grouping to the top level.
    expect(within(panel).getByRole("textbox", { name: /category name/i })).toHaveValue(
      FITTINGS.name,
    );
    fireEvent.change(within(panel).getByRole("textbox", { name: /category name/i }), {
      target: { value: "Fixings" },
    });
    fireEvent.click(within(panel).getByRole("button", { name: /save category/i }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(theWrite()).toEqual({
      path: `/api/categories/${FITTINGS.id}`,
      method: "PATCH",
      body: { name: "Fixings", parent: CABLES.id },
    });
  });

  it("says there is no way to remove one, rather than offering a control that would 404", async () => {
    warehouse(ADMINISTRATOR);
    const dialog = await addingAnItem();
    fireEvent.mouseDown(within(dialog).getByRole("combobox", { name: /category/i }));
    fireEvent.click(await screen.findByRole("option", { name: CABLES.name }));
    fireEvent.click(within(dialog).getByRole("button", { name: `Rename ${CABLES.name}` }));

    const panel = await within(dialog).findByRole("region", { name: /rename/i });
    // Saying so beats a control that would 404; EditCategory is why there is
    // no way out of this one at all.
    expect(within(panel).queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();
    expect(within(panel).getByText(/no way out of this list/i)).toBeInTheDocument();
  });

  it("offers the way back when the server wants a second look", async () => {
    warehouse(ADMINISTRATOR, () =>
      answering(
        { detail: "Sign in again to make this change.", code: "reauthentication_required" },
        403,
      ),
    );
    const dialog = await addingAnItem();
    fireEvent.click(within(dialog).getByRole("button", { name: /new category/i }));

    const panel = await within(dialog).findByRole("region", { name: /add a category/i });
    fireEvent.change(within(panel).getByRole("textbox", { name: /category name/i }), {
      target: { value: "Fittings" },
    });
    fireEvent.click(within(panel).getByRole("button", { name: /save category/i }));

    expect(await screen.findByRole("link", { name: /sign in again/i })).toHaveAttribute(
      "href",
      expect.stringContaining("/accounts/reauthenticate/"),
    );
    // And it can be put away, leaving the panel to be tried again.
    fireEvent.click(within(panel).getByRole("button", { name: /close/i }));
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: /sign in again/i })).not.toBeInTheDocument(),
    );
  });
});

describe("a place, made where a batch says where it is", () => {
  it("is not offered to a volunteer", async () => {
    warehouse(VOLUNTEER);
    renderScreen(<LocationPicker />);
    await screen.findByRole("combobox", { name: /where the stock is/i });

    expect(screen.queryByRole("button", { name: /new place/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^edit /i })).not.toBeInTheDocument();
  });

  it("is offered to an administrator, on the picker rather than a screen of its own", async () => {
    warehouse(ADMINISTRATOR);
    renderScreen(<LocationPicker />);

    expect(await screen.findByRole("button", { name: /new place/i })).toBeInTheDocument();
  });

  it("posts the place and then uses it for this batch", async () => {
    const made: Location = { ...SHELF, id: 12, name: "Shelf 9" };
    let there = [WAREHOUSE, SHELF];
    warehouse(
      ADMINISTRATOR,
      () => {
        // The row exists from here on, which is what the re-read has to find.
        there = [...there, made];
        return answering(made, 201);
      },
      () => there,
    );
    renderScreen(<LocationPicker />);
    fireEvent.click(await screen.findByRole("button", { name: /new place/i }));

    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox", { name: /^name$/i }), {
      target: { value: "Shelf 9" },
    });
    fireEvent.mouseDown(within(dialog).getByRole("combobox", { name: /what kind of place/i }));
    fireEvent.click(await screen.findByRole("option", { name: "Shelf" }));
    fireEvent.mouseDown(within(dialog).getByRole("combobox", { name: /inside/i }));
    fireEvent.click(await screen.findByRole("option", { name: WAREHOUSE.name }));
    fireEvent.click(within(dialog).getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(theWrite()).toEqual({
      path: "/api/locations",
      method: "POST",
      body: { name: "Shelf 9", parent: WAREHOUSE.id, kind: "shelf" },
    });
    // Set on the way back: making one is what somebody does when it is the
    // place they are standing in.
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: /where the stock is/i })).toHaveTextContent(
        "Shelf 9",
      ),
    );
  });

  it("does not offer to make a custody location, which needs a holder it cannot ask for", async () => {
    warehouse(ADMINISTRATOR);
    renderScreen(<LocationPicker />);
    fireEvent.click(await screen.findByRole("button", { name: /new place/i }));

    const dialog = await screen.findByRole("dialog");
    fireEvent.mouseDown(within(dialog).getByRole("combobox", { name: /what kind of place/i }));

    const kinds = await screen.findAllByRole("option");
    expect(kinds.map((one) => one.textContent)).toEqual([
      "Warehouse",
      "Hub",
      "Room",
      "Shelf",
      "Vehicle",
    ]);
  });

  it("retires a place rather than deleting it, and leaves its kind and its parent alone", async () => {
    // A shelf inside the warehouse, so the form has a parent to keep: one that
    // arrived empty would re-parent the shelf to the top level on a save that
    // was only meant to retire it.
    warehouse(ADMINISTRATOR, () => answering({ ...SHELF, active: false }));
    renderScreen(<LocationPicker />);
    fireEvent.mouseDown(await screen.findByRole("combobox", { name: /where the stock is/i }));
    fireEvent.click(await screen.findByRole("option", { name: SHELF.name }));
    fireEvent.click(screen.getByRole("button", { name: `Edit ${SHELF.name}` }));

    const dialog = await screen.findByRole("dialog");
    // The word says what happens, and a kind is not on the form at all --
    // EditLocation's header is why.
    expect(
      within(dialog).queryByRole("combobox", { name: /what kind of place/i }),
    ).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("switch", { name: /offered in the pick-list/i }));
    fireEvent.click(within(dialog).getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    const made = theWrite();
    expect(made).toMatchObject({ path: `/api/locations/${SHELF.id}`, method: "PATCH" });
    expect(made.body).toEqual({ name: SHELF.name, parent: WAREHOUSE.id, active: false });
  });

  it("does not leave the batch pointing at a place it has just retired", async () => {
    // The list serves the offered rows, so a retired one is gone from the next
    // read: keeping it as the answer to "where is this stock" would leave an
    // empty select over an id the server refuses stock arriving at.
    const retired: Location = { ...SHELF, active: false };
    let there = [WAREHOUSE, SHELF];
    warehouse(
      ADMINISTRATOR,
      () => {
        // Retired, so the next read of the offered rows does not carry it.
        there = [WAREHOUSE];
        return answering(retired);
      },
      () => there,
    );
    renderScreen(<LocationPicker />);
    fireEvent.mouseDown(await screen.findByRole("combobox", { name: /where the stock is/i }));
    fireEvent.click(await screen.findByRole("option", { name: SHELF.name }));
    fireEvent.click(screen.getByRole("button", { name: `Edit ${SHELF.name}` }));

    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("switch", { name: /offered in the pick-list/i }));
    fireEvent.click(within(dialog).getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // Nothing is chosen, so the batch says so rather than naming a shelf
    // nobody is offered -- and the control for correcting one is gone with it.
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /^edit /i })).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("combobox", { name: /where the stock is/i })).not.toHaveTextContent(
      SHELF.name,
    );
  });

  it("does not re-send active on a correction that did not touch it", async () => {
    // EditLocation's save() says what re-sending it would walk into.
    warehouse(ADMINISTRATOR, () => answering({ ...SHELF, name: "Shelf One" }));
    renderScreen(<LocationPicker />);
    fireEvent.mouseDown(await screen.findByRole("combobox", { name: /where the stock is/i }));
    fireEvent.click(await screen.findByRole("option", { name: SHELF.name }));
    fireEvent.click(screen.getByRole("button", { name: `Edit ${SHELF.name}` }));

    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox", { name: /^name$/i }), {
      target: { value: "Shelf One" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(theWrite().body).toEqual({ name: "Shelf One", parent: WAREHOUSE.id });
  });

  it("says what the server refused, in the words the server used", async () => {
    // The field-keyed body a name clash really sends; see the sentence
    // `detailOf` builds out of it.
    warehouse(ADMINISTRATOR, () =>
      answering({ name: ["location with this name already exists."] }, 400),
    );
    renderScreen(<LocationPicker />);
    fireEvent.click(await screen.findByRole("button", { name: /new place/i }));

    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox", { name: /^name$/i }), {
      target: { value: "131 Broome" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /^save$/i }));

    expect(
      await screen.findByText(/name: location with this name already exists/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    // And it is dismissible, so the form underneath is reachable again.
    fireEvent.click(within(dialog).getByRole("button", { name: /close/i }));
    await waitFor(() =>
      expect(
        screen.queryByText(/name: location with this name already exists/i),
      ).not.toBeInTheDocument(),
    );
  });
});
