/**
 * The one resolution path, tested where its callers cannot reach it.
 *
 * `Scanner.test.tsx` and `DeepLink.test.tsx` cover the ordinary answers through
 * the screens that show them. What is here is the two a screen cannot arrange:
 * a label that resolves to nothing at all, and a caller that gives up while the
 * request is in flight.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cable } from "../items/testFixtures";
import { applyCode, countsItself, recordMeasured } from "./applyCode";
import { forgetLabelCache, refreshLabelCache } from "./labelCache";
import { CABLE_LABEL, PACKET, serving } from "./testFixtures";

/** How `fetch` rejects a request whose signal was aborted. */
function aborted(): never {
  throw new DOMException("aborted", "AbortError");
}

beforeEach(() => {
  // Every case below asserts what reaches the network, so none of them may
  // start with a cache left behind by another.
  localStorage.clear();
  forgetLabelCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a code that points at nothing", () => {
  it("is offered to the search rather than reported as a failure", async () => {
    serving({ ...PACKET, kind: "item", item: null });
    expect(await applyCode("7QK3M2XV9A", vi.fn())).toEqual({
      applied: "unknown",
      code: "7QK3M2XV9A",
    });
  });

  it("says what went wrong when the item behind it cannot be read", async () => {
    serving(PACKET, { detail: "Nope." }, 503);
    expect(await applyCode("7QK3M2XV9A", vi.fn())).toEqual({
      applied: "failed",
      detail: "Nope.",
    });
  });
});

describe("a scan the caller abandoned", () => {
  /**
   * Passed on rather than answered. A screen that aborts has already gone, and
   * an outcome would be reported as a red alert reading "signal is aborted
   * without reason" -- which is what the callers' own catch blocks exist to
   * avoid.
   */
  it("rejects rather than reporting itself as a failure", async () => {
    serving(PACKET);
    const controller = new AbortController();
    controller.abort();

    await expect(applyCode("7QK3M2XV9A", vi.fn(), controller.signal)).rejects.toBeInstanceOf(
      DOMException,
    );
  });

  it("rejects when it is the item behind the label that is abandoned", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => {
        if (path.startsWith("/api/labels/")) {
          return new Response(JSON.stringify(PACKET), { status: 200 });
        }
        return aborted();
      }),
    );

    await expect(applyCode("7QK3M2XV9A", vi.fn())).rejects.toBeInstanceOf(DOMException);
  });
});

describe("a measured item", () => {
  it("is not put in the batch at the label's own quantity", async () => {
    // Decision 0011 section 5. A 305 m box of cable carries a label saying
    // 305, and a volunteer scanning one at a shelf is as likely to be
    // returning what is left of it.
    serving(CABLE_LABEL, cable);
    const dispatch = vi.fn();

    const outcome = await applyCode("4NP8R7T2WQ", dispatch);

    expect(dispatch).not.toHaveBeenCalled();
    expect(outcome).toMatchObject({
      applied: "measured",
      measured: {
        label: { quantity: 305, item: { name: "Cat6 Outdoor", unitOfMeasure: "metre" } },
      },
    });
  });

  it("keeps a replaced sticker's warning across the question", () => {
    const dispatch = vi.fn();
    const measured = {
      label: {
        code: "4NP8R7T2WQ",
        item: { id: 2, name: "Cat6 Outdoor", unitOfMeasure: "metre" },
        quantity: 305,
      },
      revoked: true,
    };

    expect(recordMeasured(measured, 12.5, dispatch)).toMatchObject({ revoked: true });
  });

  it("goes in at the amount somebody actually said", () => {
    const dispatch = vi.fn();
    const measured = {
      label: {
        code: "4NP8R7T2WQ",
        item: { id: 2, name: "Cat6 Outdoor", unitOfMeasure: "metre" },
        quantity: 305,
      },
      revoked: false,
    };

    const outcome = recordMeasured(measured, 12.5, dispatch);

    expect(dispatch).toHaveBeenCalledWith(
      expect.objectContaining({ type: "scan", quantity: 12.5 }),
    );
    expect(outcome).toMatchObject({ applied: "item", quantity: 12.5 });
  });

  it("counts an item sold by the each without asking", () => {
    expect(countsItself("each")).toBe(true);
    expect(countsItself("metre")).toBe(false);
    expect(countsItself("foot")).toBe(false);
  });
});

describe("a code the client has already seen", () => {
  it("becomes a cart line with no request at all", async () => {
    // Decision 0011 section 6, and the reason /api/labels is unpaginated:
    // twenty-four scans in a basement were forty-eight requests.
    const fetching = serving(PACKET);
    await refreshLabelCache();
    const filling = fetching.mock.calls.length;
    const dispatch = vi.fn();

    const outcome = await applyCode(PACKET.code, dispatch);

    expect(outcome).toMatchObject({ applied: "item" });
    expect(dispatch).toHaveBeenCalled();
    expect(fetching.mock.calls).toHaveLength(filling);
  });

  it("does not tell the volunteer a good sticker needs reprinting", async () => {
    // The map is live labels only and LabelMapSerializer drops revoked_at, so
    // reading it off a cached row made `undefined !== null` true and warned
    // about every sticker in the building.
    serving(PACKET);
    await refreshLabelCache();

    const outcome = await applyCode(PACKET.code, vi.fn());

    expect(outcome).toMatchObject({ applied: "item", revoked: false });
  });

  it("still resolves a code minted since the cache was filled", async () => {
    const fetching = serving(PACKET);
    await refreshLabelCache();
    const filling = fetching.mock.calls.length;

    const outcome = await applyCode("NEVER-SEEN-BEFORE", vi.fn());

    // Not in the cache, so it went and asked -- which is the fallback that
    // keeps a fresh label scannable.
    expect(fetching.mock.calls.length).toBeGreaterThan(filling);
    expect(outcome).toMatchObject({ applied: "item" });
  });
});
