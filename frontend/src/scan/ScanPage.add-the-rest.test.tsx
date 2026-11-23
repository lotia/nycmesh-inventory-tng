import { fireEvent, screen, within } from "@testing-library/preact";
import { expect, test, vi } from "vitest";
import { say } from "../shared/i18n";
import type { Problem } from "../shared/outbox";
import { PROBLEMS } from "../shared/outbox-notes";
import { telemetry } from "../shared/telemetry";
import { saved } from "../testing/fetch";
import { KEY, moveBody, outboxEntry, storeOutbox } from "../testing/saves";
import {
  expectBold,
  fillMove,
  open,
  ready,
  save,
  saves,
  server,
  storedCart,
  UUID,
} from "../testing/scan-page";
import { REST_IN_CART, REST_WITHOUT_REFUSED, TAKEN_OFF } from "./add-the-rest";
import { ADDED_BACK } from "./CartForm";
import { ADD_TO_CART } from "./Notes";
import { CART_NOT_EMPTY } from "./OlderList";
import { DISMISS } from "./OutboxList";

// The camera, faked, as the page imports it.
vi.mock("./camera", () => ({ openCamera: vi.fn() }));

const SUMMARY = "Move: 2 items from Hub to Van";
const RETIRED_NONE_LEFT: Problem = {
  names: { of: "line", n: 1 },
  kind: "retired_package_none_left",
};

/** A move of a box of radios (line 1) and 2 m of cable (line 2), refused
 * with `problems`, kept in the outbox; the page open on it. */
async function refusedWith(...problems: Problem[]) {
  storeOutbox([
    outboxEntry({
      body: moveBody({
        lines: [
          {
            item_id: 100,
            quantity: "4",
            unit: "each",
            package_id: 1000,
            package_size: "4",
          },
          { item_id: 101, quantity: "2", unit: "m", package_id: null },
        ],
      }),
      state: "refused",
      reason: { code: "refused", problems },
    }),
  ]);
  const { api, sent } = server(() => saved(78));
  const outbox = open(api);
  await ready();
  const entry = screen.getByText(SUMMARY).closest("li") as HTMLElement;
  return { entry, outbox, sent };
}

const addTheRest = (entry: HTMLElement) =>
  within(entry).queryByRole("button", { name: say(ADD_TO_CART) });

test("Add to cart, under a refused batch, puts the other lines in the cart, says each taken off and why, and Record sends them under a key of their own (Ali, 2026-11-20, inventory-tng-clvh)", async () => {
  const record = vi.spyOn(telemetry, "record");
  record.mockClear();
  const { entry, outbox, sent } = await refusedWith(RETIRED_NONE_LEFT);
  // Just above the button, plain, what it does (Ali, 2026-11-20,
  // inventory-tng-3ukw).
  const hint = within(entry).getByText(say(REST_WITHOUT_REFUSED));
  expect(hint.closest("strong, .alert, [role=alert]")).toBeNull();
  expect(hint.nextElementSibling).toBe(addTheRest(entry));
  fireEvent.click(addTheRest(entry) as HTMLElement);
  const cart = storedCart();
  expect(cart.lines.map((line: { itemId: number }) => line.itemId)).toEqual([
    101,
  ]);
  expect(cart.batchKey).toMatch(UUID);
  expect(cart.batchKey).not.toBe(KEY);
  const status = screen.getByText(say(ADDED_BACK)).closest("[role=status]");
  within(status as HTMLElement).getByText(
    say(TAKEN_OFF, {
      name: "LiteBeam 5AC",
      why: say(PROBLEMS.retired_package_none_left),
    }),
  );
  // Offered no more, nor what it does, and said to be in the cart; the
  // refusal stays listed.
  expect(addTheRest(entry)).toBeNull();
  expect(within(entry).queryByText(say(REST_WITHOUT_REFUSED))).toBeNull();
  const inCart = within(entry).getByText(say(REST_IN_CART));
  // The button keeps its room, unseen, so Dismiss does not rise to where it
  // was for a second tap to land on.
  const room = inCart.nextElementSibling as HTMLElement;
  expect(room.tagName).toBe("BUTTON");
  expect(room.getAttribute("aria-hidden")).toBe("true");
  expect(room.classList.contains("invisible")).toBe(true);
  within(entry).getByRole("button", { name: say(DISMISS) });
  expect(outbox.entries.value[0]?.reason).toMatchObject({ added: true });
  expect(
    record.mock.calls.filter(([event]) => event.kind === "rest_put_back"),
  ).toEqual([[{ kind: "rest_put_back" }]]);

  save();
  await vi.waitFor(() => expect(saves(sent)).toHaveLength(1));
  const body = await (saves(sent)[0] as Request).clone().json();
  expect(body.key).toBe(cart.batchKey);
  expect(body.lines).toEqual([
    expect.objectContaining({ item_id: 101, quantity: "2", unit: "m" }),
  ]);
  // Recorded, the words keep their room, unseen.
  await vi.waitFor(() =>
    expect(
      within(entry).getByText(say(REST_IN_CART)).getAttribute("aria-hidden"),
    ).toBe("true"),
  );
});

test("Add to cart, under a refused batch, takes only an empty cart, saying so, and is offered still", async () => {
  const { entry } = await refusedWith(RETIRED_NONE_LEFT);
  fillMove();
  fireEvent.click(addTheRest(entry) as HTMLElement);
  expectBold(within(entry).getByRole("alert"), say(CART_NOT_EMPTY));
  expect(
    storedCart().lines.map((line: { itemId: number }) => line.itemId),
  ).toEqual([101]);
  expect(screen.queryByText(say(ADDED_BACK))).toBeNull();
  expect(addTheRest(entry)).not.toBeNull();
  expect(within(entry).queryByText(say(REST_IN_CART))).toBeNull();
});

test("each refused batch's Add to cart is described by its batch's words (inventory-tng-zwnj)", async () => {
  // Two batches under one key, told apart by their cable's length.
  const refused = (to: string, cable: string) =>
    outboxEntry({
      body: moveBody({
        lines: [
          {
            item_id: 100,
            quantity: "4",
            unit: "each",
            package_id: 1000,
            package_size: "4",
          },
          { item_id: 101, quantity: cable, unit: "m", package_id: null },
        ],
      }),
      summary: { from: "Hub", to },
      state: "refused",
      reason: { code: "refused", problems: [RETIRED_NONE_LEFT] },
    });
  storeOutbox([refused("Van", "2"), refused("Shelf", "3")]);
  open(server(() => saved(78)).api);
  await ready();
  for (const to of ["Van", "Shelf"]) {
    const words = `Move: 2 items from Hub to ${to}`;
    const entry = screen.getByText(words).closest("li") as HTMLElement;
    const add = screen.getByRole("button", {
      name: say(ADD_TO_CART),
      description: words,
    });
    expect(entry.contains(add)).toBe(true);
  }
});

// Which refusals have no rest is restOf's (add-the-rest.test.ts).
test("a refusal with no rest offers no Add to cart", async () => {
  const { entry } = await refusedWith({ names: null, kind: "not_found" });
  expect(addTheRest(entry)).toBeNull();
  expect(within(entry).queryByText(say(REST_WITHOUT_REFUSED))).toBeNull();
});
