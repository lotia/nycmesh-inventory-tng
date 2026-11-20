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
import { ADDED_BACK } from "./CartForm";
import { CART_NOT_EMPTY } from "./OlderList";
import { DISMISS } from "./OutboxList";
import { RECORD_THE_REST, REST_IN_CART, TAKEN_OFF } from "./record-the-rest";

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
          { item_id: 100, quantity: "4", package_id: 1000, package_size: "4" },
          { item_id: 101, quantity: "2", package_id: null },
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

const recordTheRest = (entry: HTMLElement) =>
  within(entry).queryByRole("button", { name: say(RECORD_THE_REST) });

test("Record the rest puts the other lines in the cart, says each taken off and why, and Record sends them under a key of their own (Ali, 2026-11-20, inventory-tng-clvh)", async () => {
  const record = vi.spyOn(telemetry, "record");
  record.mockClear();
  const { entry, outbox, sent } = await refusedWith(RETIRED_NONE_LEFT);
  fireEvent.click(recordTheRest(entry) as HTMLElement);
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
  // Offered no more, and said to be in the cart; the refusal stays listed.
  expect(recordTheRest(entry)).toBeNull();
  within(entry).getByText(say(REST_IN_CART));
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
    expect.objectContaining({ item_id: 101, quantity: "2" }),
  ]);
  // Recorded, the words keep their room, unseen.
  await vi.waitFor(() =>
    expect(
      within(entry).getByText(say(REST_IN_CART)).getAttribute("aria-hidden"),
    ).toBe("true"),
  );
});

test("Record the rest takes only an empty cart, saying so, and is offered still", async () => {
  const { entry } = await refusedWith(RETIRED_NONE_LEFT);
  fillMove();
  fireEvent.click(recordTheRest(entry) as HTMLElement);
  expectBold(within(entry).getByRole("alert"), say(CART_NOT_EMPTY));
  expect(
    storedCart().lines.map((line: { itemId: number }) => line.itemId),
  ).toEqual([101]);
  expect(screen.queryByText(say(ADDED_BACK))).toBeNull();
  expect(recordTheRest(entry)).not.toBeNull();
  expect(within(entry).queryByText(say(REST_IN_CART))).toBeNull();
});

// Which refusals have no rest is restOf's (record-the-rest.test.ts).
test("a refusal with no rest offers no Record the rest", async () => {
  const { entry } = await refusedWith({ names: null, kind: "not_found" });
  expect(recordTheRest(entry)).toBeNull();
});
