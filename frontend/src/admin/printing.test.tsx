/**
 * Making stickers and putting them on a page.
 *
 * The one screen decision 0025 was written for, so half of what is asserted
 * here is about the placement: a control on the collection rather than on a
 * row, drawn from `print_label` and from nothing else, and a surface that
 * closes back to the list it was opened from. The other half is that the sheet
 * is asked for as a document — this app never lays one out, and the assertion
 * that it does not is the link it hands to the browser.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { page } from "../api/testFixtures";
import type { Item, MappedLabel } from "../api/types";
import { ItemList } from "../items/ItemList";
import { cable, zipTies } from "../items/testFixtures";
import {
  ADMINISTRATOR,
  renderScreen,
  STALE_ADMINISTRATOR,
  stubSession,
  VOLUNTEER,
  writes,
} from "../testHarness";

/** The live map, in the shape `LabelMapSerializer` sends it. */
const PACKET: MappedLabel = {
  code: "7QK3M2XV9A",
  kind: "item",
  quantity: "100.000",
  item: zipTies.id,
  location: null,
  item_name: zipTies.name,
  unit_of_measure: zipTies.unit_of_measure,
};
const BOX: MappedLabel = {
  ...PACKET,
  code: "4NP8R7T2WQ",
  quantity: "305.000",
  item: cable.id,
  item_name: cable.name,
  unit_of_measure: cable.unit_of_measure,
};
/** A wall sticker, which stands for a place and belongs on no item's sheet. */
const WALL: MappedLabel = {
  code: "5RJ9T4HB2K",
  kind: "location",
  quantity: null,
  item: null,
  location: 3,
  item_name: null,
  unit_of_measure: null,
};

const answering = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

/**
 * The catalogue, the live map, and whatever a mint should answer with.
 *
 * `catalogue` is a function of the search because one of these is about the
 * item select losing the row it was set to, and a stub that answers with both
 * items whatever was typed cannot show that. It defaults to answering with
 * both, which is what every other case here wants.
 */
function warehouse(
  session: unknown,
  labels: MappedLabel[] = [PACKET, BOX, WALL],
  mint: () => Response = () => answering({ ...PACKET, code: "M1NT3DAAA1" }, 201),
  catalogue: (search: string) => Item[] = () => [zipTies, cable],
) {
  stubSession(session, (async (path: string, init?: RequestInit) => {
    if (init?.method !== undefined && init.method !== "GET") {
      return mint();
    }
    if (path === "/api/labels") {
      return answering(labels);
    }
    return answering(
      page(...catalogue(new URL(path, "http://test").searchParams.get("search") ?? "")),
    );
  }) as unknown as typeof fetch);
}

/** The catalogue as the server narrows it: `ItemFilter` is `name icontains`. */
const narrowing = (search: string): Item[] =>
  [zipTies, cable].filter((one) => one.name.toLowerCase().includes(search.toLowerCase()));

/** Open the sheet, which is on the list rather than on a row. */
async function printing(): Promise<HTMLElement> {
  renderScreen(<ItemList />);
  await screen.findByRole("heading", { name: zipTies.name });
  fireEvent.click(screen.getByRole("button", { name: /print labels/i }));
  return screen.findByRole("dialog");
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("who is offered a sheet at all", () => {
  it("is not a volunteer", async () => {
    warehouse(VOLUNTEER);
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: zipTies.name });

    expect(screen.queryByRole("button", { name: /print labels/i })).not.toBeInTheDocument();
  });

  it("is not a session the server wants a second look at", async () => {
    warehouse(STALE_ADMINISTRATOR);
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: zipTies.name });

    expect(screen.queryByRole("button", { name: /print labels/i })).not.toBeInTheDocument();
  });

  it("is an administrator, once, on the collection rather than on a row", async () => {
    warehouse(ADMINISTRATOR);
    renderScreen(<ItemList />);
    await screen.findByRole("heading", { name: zipTies.name });

    // Two rows, one control: a sheet spans items, which is the whole reason
    // decision 0025 had to place it.
    expect(screen.getAllByRole("button", { name: /print labels/i })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: /^edit /i })).toHaveLength(2);
  });

  it("closes back to the list it was opened from", async () => {
    warehouse(ADMINISTRATOR);
    const dialog = await printing();
    fireEvent.click(within(dialog).getByRole("button", { name: /^close$/i }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // The column was never taken down, which is decision 0025 point 5.
    expect(screen.getByRole("heading", { name: zipTies.name })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /print labels/i })).toBeInTheDocument();
  });
});

describe("what goes on the sheet", () => {
  it("offers the live stickers, and not the ones that stand for a place", async () => {
    warehouse(ADMINISTRATOR);
    const dialog = await printing();

    expect(
      await within(dialog).findByRole("checkbox", { name: `Put ${PACKET.code} on the sheet` }),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("checkbox", { name: `Put ${BOX.code} on the sheet` }),
    ).toBeInTheDocument();
    // A wall code names a place, not an item, and belongs to no print run of
    // stickers for a shelf of boxes.
    expect(
      within(dialog).queryByRole("checkbox", { name: `Put ${WALL.code} on the sheet` }),
    ).not.toBeInTheDocument();
  });

  it("narrows to what was typed, by item as well as by code", async () => {
    warehouse(ADMINISTRATOR);
    const dialog = await printing();
    await within(dialog).findByRole("checkbox", { name: `Put ${PACKET.code} on the sheet` });

    fireEvent.change(within(dialog).getByRole("textbox", { name: /search items/i }), {
      target: { value: cable.name },
    });

    await waitFor(() =>
      expect(
        within(dialog).queryByRole("checkbox", { name: `Put ${PACKET.code} on the sheet` }),
      ).not.toBeInTheDocument(),
    );
    expect(
      within(dialog).getByRole("checkbox", { name: `Put ${BOX.code} on the sheet` }),
    ).toBeInTheDocument();
  });

  it("hands the codes to the browser as a document rather than laying one out", async () => {
    warehouse(ADMINISTRATOR);
    const dialog = await printing();
    fireEvent.click(
      await within(dialog).findByRole("checkbox", { name: `Put ${PACKET.code} on the sheet` }),
    );
    fireEvent.click(within(dialog).getByRole("checkbox", { name: `Put ${BOX.code} on the sheet` }));

    // A link, not a fetch: what comes back is a page to print, and the sizes
    // that decide whether a sticker scans are the server's.
    expect(within(dialog).getByText("2 on the sheet.")).toBeInTheDocument();
    const print = within(dialog).getByRole("link", { name: /print the sheet/i });
    expect(print).toHaveAttribute(
      "href",
      `/api/labels/sheet?code=${encodeURIComponent(`${PACKET.code},${BOX.code}`)}`,
    );
    expect(print).toHaveAttribute("target", "_blank");
  });

  it("offers nothing to print until something is chosen", async () => {
    warehouse(ADMINISTRATOR);
    const dialog = await printing();
    await within(dialog).findByRole("checkbox", { name: `Put ${PACKET.code} on the sheet` });

    // Disabled rather than sent: `/api/labels/sheet` refuses an empty ask with
    // a page of prose, which is not what somebody expecting stickers wants.
    expect(within(dialog).getByRole("button", { name: /print the sheet/i })).toBeDisabled();
    expect(within(dialog).getByText(/nothing on the sheet yet/i)).toBeInTheDocument();
  });

  it("says so when nothing is live yet, rather than showing an empty list", async () => {
    warehouse(ADMINISTRATOR, []);
    const dialog = await printing();

    expect(await within(dialog).findByText(/no live stickers match that/i)).toBeInTheDocument();
  });
});

describe("making the stickers in the first place", () => {
  it("mints one row per sticker and ticks what it made", async () => {
    warehouse(ADMINISTRATOR);
    const dialog = await printing();
    fireEvent.mouseDown(within(dialog).getByRole("combobox", { name: /^for$/i }));
    fireEvent.click(await screen.findByRole("option", { name: zipTies.name }));
    fireEvent.change(within(dialog).getByRole("textbox", { name: /how many stickers/i }), {
      target: { value: "2" },
    });
    fireEvent.change(within(dialog).getByRole("textbox", { name: /what one scan stands for/i }), {
      target: { value: "100" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /make them/i }));

    // One POST per sticker: the server draws each code and checks it against
    // the table, so a count is not something one write can carry.
    await waitFor(() => expect(writes("POST")).toHaveLength(2));
    expect(writes("POST")[0]).toEqual({
      path: "/api/labels",
      method: "POST",
      body: { item: zipTies.id, quantity: "100" },
    });
    // And what was made is on the sheet without anybody hunting for it: that
    // is the whole run somebody just asked for.
    expect(await within(dialog).findByText("2 on the sheet.")).toBeInTheDocument();
  });

  it("will not mint without an item, or for none of them", async () => {
    warehouse(ADMINISTRATOR);
    const dialog = await printing();

    expect(within(dialog).getByRole("button", { name: /make them/i })).toBeDisabled();

    fireEvent.mouseDown(within(dialog).getByRole("combobox", { name: /^for$/i }));
    fireEvent.click(await screen.findByRole("option", { name: zipTies.name }));
    fireEvent.change(within(dialog).getByRole("textbox", { name: /how many stickers/i }), {
      target: { value: "0" },
    });

    expect(within(dialog).getByRole("button", { name: /make them/i })).toBeDisabled();
    expect(writes("POST")).toHaveLength(0);
  });

  it("offers the way back when the server wants a second look", async () => {
    warehouse(ADMINISTRATOR, [PACKET], () =>
      answering(
        { detail: "Sign in again to make this change.", code: "reauthentication_required" },
        403,
      ),
    );
    const dialog = await printing();
    fireEvent.mouseDown(within(dialog).getByRole("combobox", { name: /^for$/i }));
    fireEvent.click(await screen.findByRole("option", { name: zipTies.name }));
    fireEvent.click(within(dialog).getByRole("button", { name: /make them/i }));

    expect(await screen.findByRole("link", { name: /sign in again/i })).toHaveAttribute(
      "href",
      expect.stringContaining("/accounts/reauthenticate/"),
    );
  });

  it("stops a run at the refusal rather than sending the rest of it", async () => {
    // Sequential, so a batch of fifty that is refused on the second is two
    // writes rather than fifty.
    let made = 0;
    warehouse(ADMINISTRATOR, [PACKET], () => {
      made += 1;
      return made === 1
        ? answering({ ...PACKET, code: "M1NT3DAAA1" }, 201)
        : answering({ detail: "Too many submissions." }, 429);
    });
    const dialog = await printing();
    fireEvent.mouseDown(within(dialog).getByRole("combobox", { name: /^for$/i }));
    fireEvent.click(await screen.findByRole("option", { name: zipTies.name }));
    fireEvent.change(within(dialog).getByRole("textbox", { name: /how many stickers/i }), {
      target: { value: "5" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /make them/i }));

    expect(await screen.findByText(/too many submissions/i)).toBeInTheDocument();
    expect(writes("POST")).toHaveLength(2);
    // And the one that was made is on the sheet rather than an orphan nobody
    // can see: pressing again would otherwise mint a second of it.
    expect(within(dialog).getByText("1 on the sheet.")).toBeInTheDocument();
  });

  it("mints for the item that is showing, not one the search has hidden", async () => {
    // PrintLabels says why a choice that falls out of the list stops being
    // the answer; this is that holding.
    warehouse(ADMINISTRATOR, [PACKET, BOX], undefined, narrowing);
    const dialog = await printing();
    fireEvent.mouseDown(within(dialog).getByRole("combobox", { name: /^for$/i }));
    fireEvent.click(await screen.findByRole("option", { name: zipTies.name }));

    fireEvent.change(within(dialog).getByRole("textbox", { name: /search items/i }), {
      target: { value: cable.name },
    });

    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: /make them/i })).toBeDisabled(),
    );
    expect(writes("POST")).toHaveLength(0);
  });
});
