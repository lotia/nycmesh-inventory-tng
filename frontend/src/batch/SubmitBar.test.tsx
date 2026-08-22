/**
 * Saving a batch, and the three things that must not happen when it fails:
 * losing the scans, double-posting them, or calling a warning an error.
 */
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useCart } from "../cart/CartProvider";
import { createCart } from "../cart/cartState";
import { STORAGE_KEY } from "../cart/cartStorage";
import { renderScreen } from "../testHarness";
import { outbox, queueBatch, sendOutbox, waiting } from "./outbox";
import { SubmitBar } from "./SubmitBar";
import { forgetWhatWasStubbed, nothing, nothingQueued } from "./testFixtures";

/** A batch ready to send, put where the provider restores it from. */
function readyBatch(): void {
  window.localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      ...createCart("key-1"),
      actorId: 7,
      locationId: 3,
      lines: [
        { itemId: 1, name: "LiteBeam", unitOfMeasure: "each", quantity: 2, lastScan: null },
        { itemId: 2, name: "Cat6 Outdoor", unitOfMeasure: "metre", quantity: 5, lastScan: null },
      ],
    }),
  );
}

/** However the batch endpoint answers, and the locations the picker lists. */
function serving(
  batch: () => Response | Promise<Response>,
  locations: { id: number; name: string }[] = [],
): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      if (path.startsWith("/api/locations")) {
        return new Response(
          JSON.stringify({
            count: locations.length,
            next: null,
            previous: null,
            results: locations,
          }),
          { status: 200 },
        );
      }
      return batch();
    }),
  );
}

/** The batch endpoint's answer, in the shape it is meant to answer in. */
function answering(
  body: unknown,
  status: number,
  locations: { id: number; name: string }[] = [],
): void {
  serving(() => new Response(JSON.stringify(body), { status }), locations);
}

/** The locations answer, and nothing at all for the batch: no network. */
function unreachable(): void {
  serving(nothing);
}

function show() {
  return renderScreen(<SubmitBar />);
}

/** Somewhere else in the app editing the batch: the item list's stepper. */
function Dropper() {
  const { dispatch } = useCart();
  return (
    <button type="button" onClick={() => dispatch({ type: "remove", itemId: 1 })}>
      Drop
    </button>
  );
}

const save = () => screen.getByRole("button", { name: /^save$/i });
const again = () => screen.getByRole("button", { name: /try again/i });

beforeEach(nothingQueued);

afterEach(forgetWhatWasStubbed);

describe("before it can be saved", () => {
  it("says what is missing rather than offering a button that will fail", () => {
    show();
    expect(screen.getByText(/say who you are/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /save/i })).not.toBeInTheDocument();
  });

  it("lists what is about to be sent", () => {
    readyBatch();
    show();
    const batch = screen.getByRole("list", { name: /this batch/i });
    expect(within(batch).getByText("LiteBeam — 2 each")).toBeInTheDocument();
    expect(within(batch).getByText("Cat6 Outdoor — 5 metres")).toBeInTheDocument();
  });
});

describe("a batch that saves", () => {
  it("clears the batch", async () => {
    readyBatch();
    answering({ id: 12, warnings: [] }, 201);
    show();
    fireEvent.click(save());
    expect(await screen.findByText(/^recorded\.$/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/nothing in this batch/i)).toBeInTheDocument());
  });

  it("treats a batch the server had already recorded as saved", async () => {
    readyBatch();
    // 200 rather than 201: the server matched the idempotency key. This is the
    // second half of a Retry whose first half arrived after all.
    answering({ id: 12, warnings: [] }, 200);
    show();
    fireEvent.click(save());
    expect(await screen.findByText(/^recorded\.$/i)).toBeInTheDocument();
  });

  it("says so even when the answer is not the shape it reads", async () => {
    // A 2xx this app cannot read; see isRecordedBatch. The server took it,
    // so the volunteer is told so rather than shown an empty screen.
    readyBatch();
    serving(() => new Response("<html>ok</html>", { status: 200 }));
    show();
    fireEvent.click(save());

    expect(await screen.findByText(/^recorded\.$/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/nothing in this batch/i)).toBeInTheDocument());
  });

  it("shows a negative balance as advice, not as a failure", async () => {
    readyBatch();
    answering(
      {
        id: 12,
        warnings: [
          { item: 1, location: 3, balance: "-4.000", detail: "131 Broome now shows -4 LiteBeam." },
        ],
      },
      201,
    );
    show();
    fireEvent.click(save());
    await waitFor(() => expect(screen.getByText(/worth a stock count/i)).toBeInTheDocument());
    expect(screen.getByText(/131 Broome now shows -4 LiteBeam\./)).toBeInTheDocument();
    // Success, not failure: the movements were recorded.
    expect(screen.getByRole("alert")).toHaveClass(/Success/i);
  });
});

describe("a batch that does not save", () => {
  it("keeps the batch and offers to try again", async () => {
    readyBatch();
    answering({ detail: "Nope." }, 503);
    show();
    fireEvent.click(save());
    expect(await screen.findByRole("alert")).toHaveTextContent("Nope.");
    expect(again()).toBeInTheDocument();
    expect(screen.getByText("LiteBeam — 2 each")).toBeInTheDocument();
  });

  it("sends the same idempotency key when it tries again, so nothing posts twice", async () => {
    readyBatch();
    answering({ detail: "Nope." }, 503);
    show();
    fireEvent.click(save());
    await screen.findByRole("alert");
    fireEvent.click(again());
    await waitFor(() => {
      const posts = (
        (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls as [string, RequestInit][]
      ).filter(([, init]) => init?.method === "POST");
      expect(posts).toHaveLength(2);
      const keys = posts.map(([, init]) => JSON.parse(String(init.body)).idempotency_key);
      expect(keys).toEqual(["key-1", "key-1"]);
    });
  });

  it("shows each complaint against the line it is about", async () => {
    readyBatch();
    answering(
      {
        detail: "Nothing was saved.",
        errors: [{ index: 1, field: "quantity", detail: "Quantity must be greater than zero." }],
      },
      400,
    );
    show();
    fireEvent.click(save());
    const batch = await screen.findByRole("list", { name: /this batch/i });
    const rows = within(batch).getAllByRole("listitem");
    expect(rows[1]).toHaveTextContent("Quantity must be greater than zero.");
    expect(rows[0]).not.toHaveTextContent("Quantity must be greater than zero.");
  });

  it("shows a complaint about the batch itself where it belongs", async () => {
    readyBatch();
    answering(
      {
        detail: "Nothing was saved.",
        errors: [{ index: null, field: "actor", detail: "That volunteer is not a choice." }],
      },
      400,
    );
    show();
    fireEvent.click(save());
    expect(await screen.findByRole("alert")).toHaveTextContent("That volunteer is not a choice.");
  });

  it("keeps each complaint with its own line when another is fixed", async () => {
    // The server answers by position in what it was sent. Dropping the first
    // line moves every position after it, so a complaint still keyed on
    // position would jump to a row it was never about -- or vanish, leaving
    // the volunteer to repair the rest blind. It is keyed on the item instead.
    readyBatch();
    answering(
      {
        detail: "Nothing was saved.",
        errors: [{ index: 1, field: "quantity", detail: "Quantity must be greater than zero." }],
      },
      400,
    );
    renderScreen(
      <>
        <SubmitBar />
        <Dropper />
      </>,
    );
    fireEvent.click(save());
    expect(await screen.findByText("Quantity must be greater than zero.")).toBeInTheDocument();

    // Drop the *first* line. The complaint was about the second.
    fireEvent.click(screen.getByRole("button", { name: "Drop" }));

    const batch = await screen.findByRole("list", { name: /this batch/i });
    const rows = within(batch).getAllByRole("listitem");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("Cat6 Outdoor");
    expect(rows[0]).toHaveTextContent("Quantity must be greater than zero.");
  });

  it("keeps the batch when it is rejected, so the scans are not lost", async () => {
    readyBatch();
    answering({ detail: "Nothing was saved.", errors: [] }, 400);
    show();
    fireEvent.click(save());
    await screen.findByRole("alert");
    expect(screen.getByText("LiteBeam — 2 each")).toBeInTheDocument();
  });
});

describe("a batch nobody answered", () => {
  it("holds it on the device and gives the cart back empty", async () => {
    readyBatch();
    unreachable();
    show();
    fireEvent.click(save());

    expect(await screen.findByText(/waiting to send/i)).toBeInTheDocument();
    // Not lost, and not in the way either: the next armful of stock can be
    // scanned while this one waits for a signal.
    expect(screen.getByText(/nothing in this batch/i)).toBeInTheDocument();
    expect(waiting(outbox())).toHaveLength(1);
  });

  it("lets the notice be put away without touching what is held", async () => {
    readyBatch();
    unreachable();
    show();
    fireEvent.click(save());
    await screen.findByText(/waiting to send/i);

    fireEvent.click(screen.getByRole("button", { name: /close/i }));

    expect(screen.queryByText(/waiting to send/i)).not.toBeInTheDocument();
    expect(waiting(outbox())).toHaveLength(1);
  });

  it("queues it under the key the cart was going to send", async () => {
    // The whole of exactly-once rests on this key surviving the handover:
    // the server matches a replay on (actor, key), so a queued batch that
    // minted a new one would be a second batch.
    readyBatch();
    unreachable();
    show();
    fireEvent.click(save());

    await screen.findByText(/waiting to send/i);
    const [held] = outbox();
    expect(held.key).toBe("key-1");
    expect(held.body).toMatchObject({ idempotency_key: "key-1", actor: 7 });
    expect(held.what).toBe("LiteBeam and Cat6 Outdoor");
  });

  it("does not queue one the server refused, which is the volunteer's to fix", async () => {
    readyBatch();
    answering({ detail: "Nothing was saved.", errors: [] }, 400);
    show();
    fireEvent.click(save());

    await screen.findByRole("alert");
    expect(outbox()).toEqual([]);
    expect(screen.getByText("LiteBeam — 2 each")).toBeInTheDocument();
  });

  it("leaves no cart behind under the key it has just handed over", async () => {
    // The queue is holding the batch from the moment Save fails, and the
    // emptied cart has to be on the device by then as well. Why that matters
    // is on `handOver` in CartProvider.
    readyBatch();
    unreachable();
    show();
    fireEvent.click(save());
    await screen.findByText(/waiting to send/i);

    const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "null");
    expect(stored.idempotencyKey).not.toBe("key-1");
    expect(stored.lines).toEqual([]);
  });

  it("keeps the batch when the device will not hold it either", async () => {
    // Both copies would otherwise be gone: nothing was stored, and the cart
    // was emptied on the strength of a write that never happened.
    readyBatch();
    unreachable();
    show();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    fireEvent.click(save());

    expect(await screen.findByText(/would not store the batch/i)).toBeInTheDocument();
    expect(screen.getByText("LiteBeam — 2 each")).toBeInTheDocument();
    expect(outbox()).toEqual([]);
  });

  it("stops saying a batch is waiting once the outbox has sent it", async () => {
    // Two notices about one batch, at opposite ends of the screen: the panel
    // above says it is in and this one still says it is waiting.
    readyBatch();
    unreachable();
    show();
    fireEvent.click(save());
    await screen.findByText(/waiting to send/i);

    answering({ id: 12, warnings: [] }, 201);
    await act(async () => {
      await sendOutbox();
    });

    expect(screen.queryByText(/waiting to send/i)).not.toBeInTheDocument();
  });

  it("sends what is waiting as soon as a save gets through", async () => {
    // A save that worked is better evidence the network is back than any
    // browser event, and the queue is drained on it.
    queueBatch({ key: "key-0", body: { idempotency_key: "key-0" }, what: "Cat6 Outdoor" });
    readyBatch();
    answering({ id: 12, warnings: [] }, 201);
    show();
    fireEvent.click(save());

    await waitFor(() => expect(waiting(outbox())).toHaveLength(0));
  });
});

describe("saying where the stock is without a camera", () => {
  it("offers the locations as a list, so a batch can be saved without scanning", async () => {
    // Decision 0011 section 1 keeps a path that requires nothing. A Save only
    // reachable by scanning would dead-end it after the batch was filled.
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        ...createCart("key-2"),
        actorId: 7,
        lines: [
          { itemId: 1, name: "LiteBeam", unitOfMeasure: "each", quantity: 2, lastScan: null },
        ],
      }),
    );
    answering({ id: 12, warnings: [] }, 201, [{ id: 3, name: "131 Broome" }]);
    show();

    expect(screen.getByText(/say where the stock is/i)).toBeInTheDocument();
    // Re-queried each time: the select is disabled until the locations arrive,
    // and MUI replaces the node rather than mutating it.
    const picker = () => screen.getByRole("combobox", { name: /where the stock is/i });
    await waitFor(() => expect(picker()).not.toHaveAttribute("aria-disabled", "true"));
    fireEvent.mouseDown(picker());
    fireEvent.click(await screen.findByRole("option", { name: "131 Broome" }));

    expect(await screen.findByRole("button", { name: /^save$/i })).toBeInTheDocument();
  });
});
