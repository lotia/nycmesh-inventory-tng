/** Record checks the cart against the catalogue as it is now before it
 * queues it (Ali, 2026-11-23, inventory-tng-h7ua). */
import { fireEvent, screen } from "@testing-library/preact";
import { describe, expect, test, vi } from "vitest";
import { say } from "../shared/i18n";
import { CATALOGUE, cable } from "../testing/catalogue";
import { answering, deferred, json, saved } from "../testing/fetch";
import {
  fillMove,
  isCatalogue,
  open,
  ready,
  save,
  saveQueued,
  saves,
} from "../testing/scan-page";
import { ENTER_AGAIN_IN } from "./batch";
import { UNIT_CHANGED } from "./CartLine";

// The camera, faked: it sees whatever a test puts in `inView`.
vi.mock("./camera", () => ({ openCamera: vi.fn() }));

const IN_FEET = {
  ...CATALOGUE,
  items: CATALOGUE.items.map((item) =>
    item.id === cable.id ? { ...item, unit: "ft" } : item,
  ),
};

/** A page whose catalogue answers as `catalogue` says, each time asked,
 * and whose batch endpoint as `save` does. */
function page(
  catalogue: () => Response | Promise<Response>,
  save: () => Response | Promise<Response> = saved,
) {
  const { api, sent } = answering((request) =>
    isCatalogue(request) ? catalogue() : save(),
  );
  return { outbox: open(api), sent };
}

/** 50 m of the cable moved from the hub to the van, by Ada. */
async function fifty() {
  await ready();
  fillMove();
  fireEvent.input(screen.getByLabelText("Loose m"), {
    target: { value: "50" },
  });
}

describe("Record checks the cart against the catalogue first", () => {
  test("a unit changed since clears what was typed in it, says so beside the box, and queues nothing until typed again", async () => {
    let shown = CATALOGUE;
    const { outbox, sent } = page(() => json(shown, 200));
    await fifty();
    shown = IN_FEET;
    save();
    await screen.findByText(
      say(ENTER_AGAIN_IN, { name: cable.name, unit: "ft" }),
    );
    screen.getByText(
      "The unit changed from m to ft. Enter the amount again in ft.",
    );
    console.log("DBG", document.body.textContent);
    expect((screen.getByLabelText("Loose ft") as HTMLInputElement).value).toBe(
      "",
    );
    expect(outbox.entries.value).toEqual([]);
    expect(saves(sent)).toEqual([]);
    // Typed again, in feet, it is queued in feet.
    fireEvent.input(screen.getByLabelText("Loose ft"), {
      target: { value: "40" },
    });
    expect(
      screen.queryByText(say(UNIT_CHANGED, { was: "m", unit: "ft" })),
    ).toBeNull();
    await saveQueued(outbox, 1);
    expect(outbox.entries.value[0]?.body.lines).toEqual([
      { item_id: cable.id, quantity: "40", unit: "ft" },
    ]);
  });

  test("a catalogue that cannot be had queues the cart as before", async () => {
    let asked = 0;
    const { outbox } = page(() => {
      asked += 1;
      return asked === 1
        ? json(CATALOGUE, 200)
        : Promise.reject(new TypeError("Failed to fetch"));
    });
    await fifty();
    await saveQueued(outbox, 1);
    expect(outbox.entries.value[0]?.body.lines).toEqual([
      { item_id: cable.id, quantity: "50", unit: "m" },
    ]);
  });

  test("one not answered in time queues the cart as before, once however often Record is tapped meanwhile", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const later = deferred();
      let asked = 0;
      // Never saved, so nothing asks for the catalogue after.
      const { outbox } = page(
        () => {
          asked += 1;
          return asked === 1 ? json(CATALOGUE, 200) : later.promise;
        },
        () => deferred().promise,
      );
      await fifty();
      save();
      await vi.waitFor(() => expect(asked).toBe(2));
      save();
      save();
      expect(outbox.entries.value).toEqual([]);
      await vi.advanceTimersByTimeAsync(3_000);
      await vi.waitFor(() => expect(outbox.entries.value).toHaveLength(1));
      // Come later, it changes nothing queued.
      later.resolve(json(IN_FEET, 200));
      await vi.advanceTimersByTimeAsync(0);
      expect(outbox.entries.value).toHaveLength(1);
      expect(asked).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });
});
