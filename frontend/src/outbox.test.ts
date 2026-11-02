import { v4 as uuid } from "uuid";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { type Body, Outbox, RETRY_MS, STORAGE_KEY } from "./outbox";
import { answering, json } from "./test-fetch";

function batch(fields: Partial<Body> = {}): Body {
  return {
    key: uuid(),
    kind: "move",
    volunteer_id: 1,
    from_location_id: 1,
    to_location_id: 2,
    lines: [{ item_id: 5, quantity: "3" }],
    ...fields,
  };
}

const saved = (id = 9) => json({ transaction_id: id }, 201);

/** Replies in turn, then this one for good. */
function replies(...queued: (() => Response | Promise<Response>)[]) {
  return () => (queued.shift() ?? saved)();
}

/** Let the outbox's sends run. */
async function settled(outbox: Outbox) {
  await vi.waitFor(() =>
    expect(
      outbox.entries.value.some((e) => e.state === "waiting" && !e.note),
    ).toBe(false),
  );
}

const stored = () => JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");

describe("queueing", () => {
  test("a batch is kept, sent at once, and settles as saved", async () => {
    const { api, sent } = answering(() => saved(41));
    const outbox = new Outbox(api);
    const body = batch();
    expect(outbox.queue(body, "Move: 1 item")).toBe(true);
    await settled(outbox);
    expect(await sent[0]?.json()).toEqual(body);
    expect(new URL(sent[0]?.url ?? "").pathname).toBe("/api/transactions");
    const [entry] = outbox.entries.value;
    expect(entry).toMatchObject({
      state: "saved",
      transactionId: 41,
      what: "Move: 1 item",
    });
    expect(stored()).toEqual(outbox.entries.value);
  });

  test("the same key twice is one batch, sent once", async () => {
    const { api, sent } = answering(() => saved());
    const outbox = new Outbox(api);
    const body = batch();
    outbox.queue(body, "a");
    outbox.queue(body, "a");
    await settled(outbox);
    expect(outbox.entries.value).toHaveLength(1);
    expect(sent).toHaveLength(1);
  });

  test("a browser that will not store it says so, and nothing is sent", () => {
    const { api, sent } = answering(() => saved());
    const full = {
      ...localStorage,
      getItem: () => null,
      setItem: () => {
        throw new DOMException("full", "QuotaExceededError");
      },
    } as unknown as Storage;
    const outbox = new Outbox(api, full);
    expect(outbox.queue(batch(), "a")).toBe(false);
    expect(sent).toEqual([]);
  });
});

describe("sending again", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => vi.useRealTimers());

  test.each([
    ["a 503", () => json({ reason: "database_unavailable" }, 503)],
    ["a proxy's 502", () => new Response("<html>502</html>", { status: 502 })],
    ["no answer", () => Promise.reject(new TypeError("Failed to fetch"))],
    ["a 500", () => new Response("oops", { status: 500 })],
  ])(
    "after %s, it waits, then goes again with the same body",
    async (_name, first) => {
      const { api, sent } = answering(replies(first));
      const outbox = new Outbox(api);
      const body = batch();
      outbox.queue(body, "a");
      await vi.waitFor(() =>
        expect(outbox.entries.value[0]?.waitingFor).toBe("network"),
      );
      expect(outbox.entries.value[0]?.state).toBe("waiting");
      await vi.advanceTimersByTimeAsync(RETRY_MS);
      await vi.waitFor(() =>
        expect(outbox.entries.value[0]?.state).toBe("saved"),
      );
      const bodies = await Promise.all(sent.map((request) => request.json()));
      expect(bodies).toEqual([body, body]);
    },
  );

  test("an unregistered device waits for registering, then sends the same batch", async () => {
    const { api, sent } = answering(
      replies(() => json({ reason: "unregistered" }, 403)),
    );
    const outbox = new Outbox(api);
    const body = batch();
    outbox.queue(body, "a");
    await vi.waitFor(() =>
      expect(outbox.entries.value[0]?.waitingFor).toBe("registration"),
    );
    // No timer: nothing changes until the device registers.
    await vi.advanceTimersByTimeAsync(RETRY_MS * 3);
    expect(sent).toHaveLength(1);
    await outbox.send();
    expect(outbox.entries.value[0]?.state).toBe("saved");
    expect(await sent[1]?.json()).toEqual(body);
  });

  test("a revoked device is told to register again", async () => {
    const { api } = answering(() => json({ reason: "revoked" }, 403));
    const outbox = new Outbox(api);
    outbox.queue(batch(), "a");
    await vi.waitFor(() =>
      expect(outbox.entries.value[0]?.note).toContain("withdrawn"),
    );
  });

  test("a batch that cannot go now holds back the ones after it", async () => {
    const { api, sent } = answering(
      replies(() => json({ reason: "database_unavailable" }, 503)),
    );
    const outbox = new Outbox(api);
    const first = batch();
    const second = batch();
    outbox.queue(first, "first");
    await vi.waitFor(() =>
      expect(outbox.entries.value[0]?.waitingFor).toBe("network"),
    );
    outbox.queue(second, "second");
    await vi.waitFor(() =>
      expect(outbox.entries.value.map((e) => e.state)).toEqual([
        "saved",
        "saved",
      ]),
    );
    const keys = await Promise.all(
      sent.map(async (request) => (await request.json()).key),
    );
    // The first goes again before the second goes at all.
    expect(keys).toEqual([first.key, first.key, second.key]);
  });
});

describe("refusals", () => {
  test("a reused key is refused, and the next batch still goes", async () => {
    const { api } = answering(
      replies(() => json({ reason: "key_reused" }, 409)),
    );
    const outbox = new Outbox(api);
    outbox.queue(batch(), "first");
    outbox.queue(batch(), "second");
    await vi.waitFor(() =>
      expect(outbox.entries.value.map((e) => e.state)).toEqual([
        "refused",
        "saved",
      ]),
    );
    expect(outbox.entries.value[0]?.note).toContain("Please report this");
  });

  test("a 422 says which line, in the server's words", async () => {
    const detail = [
      {
        type: "not_found",
        loc: ["body", "lines", 1, "item_id"],
        msg: "There is no such row",
      },
      {
        type: "value_error",
        loc: ["body"],
        msg: "Value error, a move has only a from and to location",
      },
    ];
    const { api } = answering(() => json({ detail }, 422));
    const outbox = new Outbox(api);
    outbox.queue(batch(), "a");
    await vi.waitFor(() =>
      expect(outbox.entries.value[0]?.state).toBe("refused"),
    );
    expect(outbox.entries.value[0]?.note).toBe(
      "Not saved. Line 2: There is no such row Value error, a move has only a from and to location",
    );
  });

  test("a settled batch can be dismissed; a waiting one cannot", async () => {
    const { api } = answering(
      replies(
        () => json({ detail: [] }, 422),
        () => json({ reason: "unregistered" }, 403),
      ),
    );
    const outbox = new Outbox(api);
    const refused = batch();
    const waiting = batch();
    outbox.queue(refused, "a");
    await vi.waitFor(() =>
      expect(outbox.entries.value[0]?.state).toBe("refused"),
    );
    outbox.queue(waiting, "b");
    await vi.waitFor(() =>
      expect(outbox.entries.value[1]?.waitingFor).toBe("registration"),
    );
    outbox.dismiss(refused.key);
    outbox.dismiss(waiting.key);
    expect(outbox.entries.value.map((e) => e.body.key)).toEqual([waiting.key]);
  });
});

describe("storage", () => {
  test("waiting batches outlive the page", async () => {
    const { api } = answering(() => json({ reason: "unregistered" }, 403));
    const body = batch();
    const before = new Outbox(api);
    before.queue(body, "a");
    await vi.waitFor(() =>
      expect(before.entries.value[0]?.waitingFor).toBe("registration"),
    );
    const after = new Outbox(answering(() => saved()).api);
    expect(after.entries.value.map((e) => e.body)).toEqual([body]);
    await after.send();
    expect(after.entries.value[0]?.state).toBe("saved");
  });

  test("an unreadable entry is dropped, and only that one", () => {
    const good = {
      body: batch(),
      what: "a",
      queuedAt: 1,
      state: "waiting",
      waitingFor: null,
      note: null,
      transactionId: null,
    };
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify([good, { ...good, body: "not a batch" }, 7, null]),
    );
    const outbox = new Outbox(answering(() => saved()).api);
    expect(outbox.entries.value).toEqual([good]);
  });

  test.each(["not json", '{"an": "object"}'])(
    "storage holding %j reads as empty",
    (value) => {
      localStorage.setItem(STORAGE_KEY, value);
      expect(new Outbox(answering(() => saved()).api).entries.value).toEqual(
        [],
      );
    },
  );

  test("only the ten newest saved batches are kept", async () => {
    let id = 0;
    const { api } = answering(() => saved(++id));
    const outbox = new Outbox(api);
    for (let n = 0; n < 12; n += 1) {
      outbox.queue(batch(), `batch ${n}`);
      await settled(outbox);
    }
    expect(outbox.entries.value.map((e) => e.what)).toEqual(
      Array.from({ length: 10 }, (_, n) => `batch ${n + 2}`),
    );
  });

  test("another tab's batch is not lost when this tab writes", async () => {
    const { api } = answering(() => json({ reason: "unregistered" }, 403));
    const here = new Outbox(api);
    const there = new Outbox(api);
    there.queue(batch(), "there");
    here.queue(batch(), "here");
    await vi.waitFor(() => expect(stored()).toHaveLength(2));
    expect(here.entries.value.map((e) => e.what)).toEqual(["there", "here"]);
  });
});

describe("listening", () => {
  test("coming back online sends what waits", async () => {
    const { api, sent } = answering(
      replies(() => Promise.reject(new TypeError("Failed to fetch"))),
    );
    const outbox = new Outbox(api);
    const stop = outbox.listen();
    outbox.queue(batch(), "a");
    await vi.waitFor(() =>
      expect(outbox.entries.value[0]?.waitingFor).toBe("network"),
    );
    window.dispatchEvent(new Event("online"));
    await vi.waitFor(() =>
      expect(outbox.entries.value[0]?.state).toBe("saved"),
    );
    expect(sent).toHaveLength(2);
    stop();
  });

  test("another tab's change shows here", () => {
    const outbox = new Outbox(answering(() => saved()).api);
    const stop = outbox.listen();
    const other = {
      body: batch(),
      what: "from the other tab",
      queuedAt: 1,
      state: "saved",
      waitingFor: null,
      note: null,
      transactionId: 3,
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify([other]));
    window.dispatchEvent(new StorageEvent("storage", { key: STORAGE_KEY }));
    expect(outbox.entries.value).toEqual([other]);
    stop();
  });

  test("sending twice at once sends each batch once", async () => {
    const { api, sent } = answering(() => saved());
    const outbox = new Outbox(api);
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify([
        {
          body: batch(),
          what: "a",
          queuedAt: 1,
          state: "waiting",
          waitingFor: null,
          note: null,
          transactionId: null,
        },
      ]),
    );
    await Promise.all([outbox.send(), outbox.send()]);
    expect(sent).toHaveLength(1);
  });
});
