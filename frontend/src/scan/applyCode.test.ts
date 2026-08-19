/**
 * The one resolution path, tested where its callers cannot reach it.
 *
 * `Scanner.test.tsx` and `DeepLink.test.tsx` cover the ordinary answers through
 * the screens that show them. What is here is the two a screen cannot arrange:
 * a label that resolves to nothing at all, and a caller that gives up while the
 * request is in flight.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cable } from "../items/testFixtures";
import { applyCode, countsItself, recordMeasured } from "./applyCode";
import { CABLE_LABEL, PACKET, serving } from "./testFixtures";

/** How `fetch` rejects a request whose signal was aborted. */
function aborted(): never {
  throw new DOMException("aborted", "AbortError");
}

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
