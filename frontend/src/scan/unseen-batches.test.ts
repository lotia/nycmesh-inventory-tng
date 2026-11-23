import { describe, expect, test } from "vitest";
import type { Draft } from "../shared/outbox";
import { CATALOGUE, cable } from "../testing/catalogue";
import { keyed } from "../testing/saves";
import { sealedHere } from "./cart";
import { offered } from "./offered";
import { keysToAsk, sealedCounts, unseen } from "./unseen-batches";

describe("turning a cart into a batch", () => {
  test("sealed counts follow the batches the catalogue does not show yet", () => {
    // The catalogue: 3 spools sealed at the hub, 1 in the van.
    const batch = (draft: Partial<Draft>) =>
      keyed({ kind: "move", volunteer_id: 1, lines: [], ...draft });
    const spools = (n: number, extra = {}) => ({
      item_id: 101,
      quantity: String(305 * n),
      unit: "m",
      package_id: 1001,
      package_size: "305",
      ...extra,
    });
    const at = (counts: Map<string, number>, place: number) =>
      counts.get(`1001@${place}`) ?? 0;
    const counts = sealedCounts(CATALOGUE, [
      // Two opened at the hub, each its own open package.
      {
        state: "waiting" as const,
        queuedAt: 0,
        body: batch({
          kind: "open",
          from_location_id: 10,
          to_location_id: 10,
          lines: [
            spools(1, {
              open_package_key: "11111111-1111-4111-8111-111111111111",
            }),
            spools(1, {
              open_package_key: "22222222-2222-4222-8222-222222222222",
            }),
          ],
        }),
      },
      // One moved from the van to the hub, and one received at the van;
      // a cut from an open package moves no sealed one.
      {
        state: "saved" as const,
        queuedAt: 0,
        body: batch({
          from_location_id: 12,
          to_location_id: 10,
          lines: [
            spools(1),
            {
              item_id: 101,
              quantity: "20",
              unit: "m",
              package_id: 1001,
              open_package_key: "33333333-3333-4333-8333-333333333333",
            },
          ],
        }),
      },
      {
        state: "waiting" as const,
        queuedAt: 0,
        body: batch({
          kind: "receipt",
          to_location_id: 12,
          lines: [spools(2)],
        }),
      },
      // One taken to an install from the hub, and loose cable: no spool.
      {
        state: "waiting" as const,
        queuedAt: 0,
        body: batch({
          kind: "install",
          from_location_id: 10,
          lines: [
            spools(1),
            { item_id: 101, quantity: "5", unit: "m" },
            // A package the catalogue has no more: not counted.
            spools(1, { package_id: 1009 }),
          ],
        }),
      },
    ]);
    expect(at(counts, 10)).toBe(3 - 2 + 1 - 1);
    expect(at(counts, 12)).toBe(1 - 1 + 2);
    // Counted in a size the spool had before a resize: refused, so none.
    const resized = sealedCounts(CATALOGUE, [
      {
        state: "waiting" as const,
        queuedAt: 0,
        body: batch({
          kind: "receipt",
          to_location_id: 12,
          lines: [spools(2, { package_size: "610" })],
        }),
      },
      // Typed in ft, the spool's cable counted in m: refused, so none.
      {
        state: "waiting" as const,
        queuedAt: 0,
        body: batch({
          kind: "receipt",
          to_location_id: 12,
          lines: [spools(1, { unit: "ft" })],
        }),
      },
      // Stored by a build from before sizes were sent: refused, so none.
      {
        state: "waiting" as const,
        queuedAt: 0,
        body: batch({
          kind: "receipt",
          to_location_id: 12,
          lines: [spools(1, { package_size: null })],
        }),
      },
    ]);
    expect(at(resized, 12)).toBe(1);
    // As stored by another build, unreadable: skipped, never thrown.
    const unreadable = {
      state: "waiting" as const,
      queuedAt: 0,
      body: batch({
        from_location_id: 10,
        to_location_id: 12,
        lines: [
          spools(1, { quantity: "1.2345" }),
          {
            item_id: 101,
            quantity: "abc",
            unit: "m",
            open_package_key: "11111111-1111-4111-8111-111111111111",
          },
        ],
      }),
    };
    expect(at(sealedCounts(CATALOGUE, [unreadable]), 12)).toBe(1);
    expect(offered(CATALOGUE, [unreadable])).toEqual(offered(CATALOGUE, []));
    // The last sealed at the hub opened: the next opening there fills none.
    const opened = sealedCounts(CATALOGUE, [
      {
        state: "waiting" as const,
        queuedAt: 0,
        body: batch({
          kind: "open",
          from_location_id: 10,
          to_location_id: 10,
          lines: [spools(3)],
        }),
      },
    ]);
    expect(sealedHere(cable, 10, opened)).toBe(undefined);
    expect(sealedHere(cable, 12, opened)).toBe(cable.packages[0]);
  });

  test("unseen: batches waiting or saved that the catalogue does not show", () => {
    const entry = (state: "waiting" | "saved" | "refused" | "too_old") => ({
      state,
      queuedAt: 0,
      body: keyed({ kind: "move", volunteer_id: 1, lines: [] }),
    });
    const waiting = entry("waiting");
    const shown = entry("saved");
    const saved = entry("saved");
    const refused = entry("refused");
    // Listed with what older builds left (older-builds.ts), never sent.
    const tooOld = entry("too_old");
    const outbox = [waiting, shown, saved, refused, tooOld];
    const catalogue = { ...CATALOGUE, saved: [shown.body.key] };
    expect(unseen(outbox, catalogue)).toEqual([waiting, saved]);
    // Asked about: saved ones first, then those waiting.
    expect(keysToAsk(outbox)).toEqual([
      shown.body.key,
      saved.body.key,
      waiting.body.key,
    ]);
  });
});
