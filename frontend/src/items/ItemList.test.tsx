/**
 * The item list, exercised the way a volunteer uses it: type, tap, look.
 *
 * Everything is found by role and accessible name, so what these assert is
 * what somebody standing at a shelf can actually perceive.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { page } from "../api/testFixtures";
import type { Item } from "../api/types";
import { renderScreen } from "../testHarness";
import { ItemList } from "./ItemList";
import { formatQuantity } from "./quantity";
import { cable, zipTies } from "./testFixtures";

/**
 * Count what each row draws, without asking a row to count itself.
 *
 * `formatQuantity` is called at least once by every `ItemRow` that renders --
 * the "on hand" line -- and the real implementation is kept, so nothing else
 * in this file behaves differently for it. It is the only handle a test has on
 * how many rows React actually drew, which is the thing "does not redraw the
 * other forty-nine" is about.
 */
vi.mock("./quantity", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./quantity")>();
  return { ...actual, formatQuantity: vi.fn(actual.formatQuantity) };
});

function serving(...items: Item[]): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      const search = new URL(path, "http://test").searchParams.get("search");
      const matching = search
        ? items.filter((item) => item.name.toLowerCase().includes(search.toLowerCase()))
        : items;
      return new Response(JSON.stringify(page(...matching)), { status: 200 });
    }),
  );
}

function show() {
  return renderScreen(<ItemList />);
}

/** The row for one item, so an assertion cannot match the other one's control. */
function rowFor(name: string): HTMLElement {
  return screen.getByRole("heading", { name }).closest("li") as HTMLElement;
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the item list", () => {
  it("shows every item in the catalogue", async () => {
    serving(zipTies, cable);
    show();
    expect(await screen.findByRole("heading", { name: "Zip Ties Reusable" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Cat6 Outdoor" })).toBeInTheDocument();
  });

  it("shows the count behind each item, added up across locations", async () => {
    serving(zipTies, cable);
    show();
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });
    expect(within(rowFor("Zip Ties Reusable")).getByText("500 on hand")).toBeInTheDocument();
  });

  it("narrows to what was typed", async () => {
    serving(zipTies, cable);
    show();
    await screen.findByRole("heading", { name: "Cat6 Outdoor" });
    fireEvent.change(screen.getByRole("textbox", { name: /search items/i }), {
      target: { value: "cat6" },
    });
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Zip Ties Reusable" })).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("heading", { name: "Cat6 Outdoor" })).toBeInTheDocument();
  });

  it("says so when a search matches nothing, rather than showing an empty page", async () => {
    serving(zipTies);
    show();
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });
    fireEvent.change(screen.getByRole("textbox", { name: /search items/i }), {
      target: { value: "fibre" },
    });
    expect(await screen.findByText(/nothing matches/i)).toBeInTheDocument();
  });

  it("says so when the catalogue is empty", async () => {
    serving();
    show();
    expect(await screen.findByText(/no items in the catalogue yet/i)).toBeInTheDocument();
  });

  it("shows what went wrong rather than an empty list", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "Nope." }), { status: 403 })),
    );
    show();
    expect(await screen.findByRole("alert")).toHaveTextContent("Nope.");
  });
});

describe("adding to the batch", () => {
  it("puts a line in the batch when the stepper is tapped", async () => {
    serving(zipTies);
    show();
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });
    fireEvent.click(screen.getByRole("button", { name: "Add one Zip Ties Reusable" }));
    expect(screen.getByRole("textbox", { name: /quantity of zip ties reusable/i })).toHaveValue(
      "1",
    );
  });

  it("offers the item's packaging as one tap, because +1 is no use for hundreds", async () => {
    serving(zipTies);
    show();
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });
    fireEvent.click(screen.getByRole("button", { name: "Add a packet of 100 Zip Ties Reusable" }));
    expect(screen.getByText("100 each (1 packet of 100)")).toBeInTheDocument();
  });

  it("does not offer a label meaning one as packaging", async () => {
    serving(zipTies);
    show();
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });
    expect(
      screen.queryByRole("button", { name: "Add a packet of 1 Zip Ties Reusable" }),
    ).not.toBeInTheDocument();
  });

  it("offers no packaging for an item that has none", async () => {
    serving(cable);
    show();
    await screen.findByRole("heading", { name: "Cat6 Outdoor" });
    expect(within(rowFor("Cat6 Outdoor")).queryAllByRole("button", { name: /packet/i })).toEqual(
      [],
    );
  });

  it("spells the quantity out in the item's own unit", async () => {
    serving(cable);
    show();
    await screen.findByRole("heading", { name: "Cat6 Outdoor" });
    fireEvent.click(screen.getByRole("button", { name: "Add one Cat6 Outdoor" }));
    expect(screen.getByText("1 metre")).toBeInTheDocument();
  });

  it("lets the quantity be corrected in one tap", async () => {
    serving(zipTies);
    show();
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });
    fireEvent.click(screen.getByRole("button", { name: "Add one Zip Ties Reusable" }));
    const field = screen.getByRole("textbox", { name: /quantity of zip ties reusable/i });
    fireEvent.change(field, { target: { value: "7" } });
    expect(screen.getByText("7 each")).toBeInTheDocument();
  });

  it("lets a decimal quantity be typed a character at a time", async () => {
    // A measured item is what the typed field exists for, and "1.5" is typed
    // through "1." -- which reads back as a bare 1 and would take the point
    // away again the moment it was entered.
    serving(cable);
    show();
    await screen.findByRole("heading", { name: "Cat6 Outdoor" });
    fireEvent.click(screen.getByRole("button", { name: "Add one Cat6 Outdoor" }));
    const field = screen.getByRole("textbox", { name: /quantity of cat6 outdoor/i });
    fireEvent.change(field, { target: { value: "1." } });
    fireEvent.change(field, { target: { value: "1.5" } });
    expect(screen.getByText("1.5 metres")).toBeInTheDocument();
  });

  it("keeps the line while the box is empty, so it can be cleared and retyped", async () => {
    // An empty box reads back as zero, and zero takes the line out of the
    // cart -- so clearing it to type a new number would delete the field the
    // volunteer is typing into.
    serving(zipTies);
    show();
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });
    fireEvent.click(screen.getByRole("button", { name: "Add one Zip Ties Reusable" }));
    const field = screen.getByRole("textbox", { name: /quantity of zip ties reusable/i });
    fireEvent.change(field, { target: { value: "" } });
    expect(field).toBeInTheDocument();
    fireEvent.change(field, { target: { value: "12" } });
    expect(screen.getByText("12 each")).toBeInTheDocument();
  });

  it("shows the cart's own number again once the box is left", async () => {
    serving(zipTies);
    show();
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });
    fireEvent.click(screen.getByRole("button", { name: "Add one Zip Ties Reusable" }));
    const field = screen.getByRole("textbox", { name: /quantity of zip ties reusable/i });
    fireEvent.change(field, { target: { value: "" } });
    fireEvent.blur(field);
    expect(field).toHaveValue("1");
  });

  it("adds to the line already there rather than starting a second one", async () => {
    serving(zipTies);
    show();
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });
    fireEvent.click(screen.getByRole("button", { name: "Add a packet of 100 Zip Ties Reusable" }));
    fireEvent.click(screen.getByRole("button", { name: "Add one Zip Ties Reusable" }));
    expect(screen.getByText("101 each")).toBeInTheDocument();
  });

  it("takes the line out of the batch when it is stepped down to nothing", async () => {
    serving(zipTies);
    show();
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });
    fireEvent.click(screen.getByRole("button", { name: "Add one Zip Ties Reusable" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove one Zip Ties Reusable" }));
    expect(
      screen.queryByRole("textbox", { name: /quantity of zip ties reusable/i }),
    ).not.toBeInTheDocument();
  });

  it("cannot step down an item that is not in the batch", async () => {
    serving(zipTies);
    show();
    await screen.findByRole("heading", { name: "Zip Ties Reusable" });
    expect(screen.getByRole("button", { name: "Remove one Zip Ties Reusable" })).toBeDisabled();
  });
});

describe("a page of fifty rows", () => {
  it("does not redraw the other forty-nine when one line changes", async () => {
    // A row that read the cart context re-rendered on every scan, rebuilding
    // its packaging chips and re-summing its balances fifty times over. It is
    // told its own quantity instead, so `memo` skips the rest.
    const many = Array.from({ length: 50 }, (_, index) => ({
      ...zipTies,
      id: index + 1,
      name: `Item ${index + 1}`,
    }));
    serving(...many);
    show();
    await screen.findByRole("heading", { name: "Item 1" });
    expect(screen.getAllByRole("heading", { name: /^Item / })).toHaveLength(many.length);

    const drawn = () => vi.mocked(formatQuantity).mock.calls.length;
    const before = drawn();
    fireEvent.click(screen.getByRole("button", { name: "Add one Item 1" }));

    // Fewer calls than there are rows, and every row that renders costs at
    // least one: so this cannot pass with every row redrawn, which is what it
    // did before `ItemRow` was told its own number instead of reading the
    // cart. Without `memo` this is fifty rows' worth rather than one's.
    expect(drawn() - before).toBeLessThan(many.length);

    // And the row that did change is the one that changed.
    expect(screen.getByRole("textbox", { name: /quantity of item 1 in the batch/i })).toHaveValue(
      "1",
    );
    expect(
      screen.queryAllByRole("textbox", { name: /quantity of item .* in the batch/i }),
    ).toHaveLength(1);
    expect(screen.getAllByRole("heading", { name: /^Item / })).toHaveLength(many.length);
  });
});
