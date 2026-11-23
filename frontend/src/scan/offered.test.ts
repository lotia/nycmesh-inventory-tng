import { describe, expect, test } from "vitest";
import type { Draft } from "../shared/outbox";
import { CATALOGUE, openAt, openSpool } from "../testing/catalogue";
import { keyed } from "../testing/saves";
import { offered, offeredAt } from "./offered";
import { unseen } from "./unseen-batches";

const SHOWN = "99999999-9999-4999-8999-999999999999";

describe("turning a cart into a batch", () => {
  test("open packages offered: the catalogue's, and as waiting batches leave them", () => {
    const known = openSpool();
    const fresh = "33333333-3333-4333-8333-333333333333";
    const catalogue = { ...CATALOGUE, open_packages: [known] };
    const opening: Draft = {
      kind: "open",
      volunteer_id: 1,
      from_location_id: 10,
      to_location_id: 10,
      lines: [
        {
          item_id: 101,
          quantity: "305",
          package_id: 1001,
          package_size: "305",
          open_package_key: fresh,
        },
        // Sent already, so the catalogue's: not twice.
        {
          item_id: 101,
          quantity: "305",
          package_id: 1001,
          package_size: "305",
          open_package_key: known.key,
        },
        {
          item_id: 101,
          quantity: "305",
          package_id: 1001,
          package_size: "305",
        },
        // Counted in another size, or in none, as an older build's: the
        // server refuses them, so they open nothing to offer.
        {
          item_id: 101,
          quantity: "610",
          package_id: 1001,
          package_size: "610",
          open_package_key: "55555555-5555-4555-8555-555555555555",
        },
        {
          item_id: 101,
          quantity: "305",
          package_id: 1001,
          open_package_key: "66666666-6666-4666-8666-666666666666",
        },
      ],
    };
    const cut: Draft = {
      kind: "move",
      volunteer_id: 1,
      from_location_id: 10,
      to_location_id: 12,
      lines: [
        { item_id: 101, quantity: "20", open_package_key: known.key },
        { item_id: 101, quantity: "305", open_package_key: fresh, whole: true },
        { item_id: 101, quantity: "1" },
        // Opened on another phone: the catalogue's, once loaded again.
        {
          item_id: 101,
          quantity: "1",
          open_package_key: "44444444-4444-4444-8444-444444444444",
        },
      ],
    };
    const queuedAt = Date.parse("2026-11-04T09:00:00Z");
    const outbox = [
      { state: "waiting" as const, body: keyed(opening), queuedAt },
      { state: "waiting" as const, body: keyed(cut), queuedAt },
      // Saved, and the catalogue says it shows it.
      { state: "saved" as const, body: { ...cut, key: SHOWN }, queuedAt },
    ];
    const all = offered(
      { ...catalogue, saved: [SHOWN] },
      unseen(outbox, { ...catalogue, saved: [SHOWN] }),
    );
    expect(offeredAt(all, 101, 10)).toEqual([{ ...known, left: 100000 }]);
    expect(offeredAt(all, 101, 12)).toEqual([
      openAt(0, 305000, {
        key: fresh,
        location_id: 12,
        opened_at: "2026-11-04T09:00:00.000Z",
      }),
    ]);
  });

  test("an item's open packages at a place: fewest left first, ties by id, those showing nothing last", () => {
    // As on the stock page (Ali, 2026-11-18, inventory-tng-qskv). One
    // opened on this phone has no id yet: the server gives it the next,
    // so it follows those of the same length.
    const at = (id: number, key: string, left: number) =>
      openAt(id, left, { key });
    const all = [
      at(0, "waiting-a", 40000),
      at(6, "below", -4000),
      at(9, "nine", 40000),
      at(5, "full", 120000),
      at(8, "none", 0),
      at(7, "seven", 40000),
      at(0, "waiting-b", 40000),
      { ...at(4, "elsewhere", 1000), location_id: 12 },
    ];
    expect(offeredAt(all, 101, 10).map((each) => each.key)).toEqual([
      "seven",
      "nine",
      "waiting-a",
      "waiting-b",
      "full",
      "below",
      "none",
    ]);
  });

  test("an item's short ends at a place follow its usable open packages, fewest left first, ties by id", () => {
    // Too short to use, so after those a cut can come from (Ali,
    // 2026-11-18, inventory-tng-qskv); those showing nothing stay last.
    const at = (id: number, left: number, short_below: string | null) =>
      openAt(id, left, { short_below });
    const all = [
      at(1, 0, "15.000"),
      at(2, 12000, "15.000"),
      at(3, 120000, "15.000"),
      at(4, 5000, "15.000"),
      at(5, 15000, "15.000"),
      at(6, 12000, "15.000"),
      at(7, 5000, null),
      at(0, 5000, "15.000"),
    ];
    expect(offeredAt(all, 101, 10).map((each) => each.id)).toEqual([
      7, 5, 3, 4, 0, 2, 6, 1,
    ]);
  });

  test("an opening waiting of a package the catalogue has no more is named 'package'", () => {
    const opening: Draft = {
      kind: "open",
      volunteer_id: 1,
      from_location_id: 10,
      to_location_id: 10,
      lines: [
        {
          item_id: 101,
          quantity: "305",
          package_id: 1009,
          package_size: "305",
          open_package_key: "33333333-3333-4333-8333-333333333333",
        },
      ],
    };
    const [made] = offered(CATALOGUE, [
      { state: "waiting" as const, body: keyed(opening), queuedAt: 0 },
    ]);
    expect(made?.package_name).toBe("package");
    // Kept by an older build, in no size: refused, so offered nowhere.
    const sizeless = {
      ...opening,
      lines: opening.lines.map(({ package_size: _, ...line }) => line),
    };
    expect(
      offered(CATALOGUE, [
        { state: "waiting" as const, body: keyed(sizeless), queuedAt: 0 },
      ]),
    ).toEqual([]);
  });

  test("an open package said used up is offered no more", () => {
    const known = openSpool();
    const { key } = known;
    const report: Draft = {
      kind: "report",
      volunteer_id: 1,
      from_location_id: 10,
      lines: [],
      used_up: [key],
    };
    const catalogue = { ...CATALOGUE, open_packages: [known] };
    expect(
      offered(catalogue, [
        { state: "waiting" as const, body: keyed(report), queuedAt: 0 },
      ]),
    ).toEqual([]);
  });
});
