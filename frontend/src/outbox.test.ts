import { v4 as uuid } from "uuid";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  type Draft,
  type Entry,
  newEntry,
  Outbox,
  REPORT_AFTER,
  RETRY_MS,
  STORAGE_KEY,
} from "./outbox";
import { telemetry } from "./telemetry";
import { answering, json, saved } from "./test-fetch";

function draft(fields: Partial<Draft> = {}): Draft {
  return {
    kind: "move",
    volunteer_id: 1,
    from_location_id: 1,
    to_location_id: 2,
    lines: [{ item_id: 5, quantity: "3" }],
    ...fields,
  };
}

/** An entry as storage might hold it. */
function entry(fields: Partial<Entry> = {}): Entry {
  return { ...newEntry({ ...draft(), key: uuid() }, "a", 1), ...fields };
}

function store(...entries: unknown[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
}

/** Storage that reads but refuses every write. */
const full = {
  getItem: (key: string) => localStorage.getItem(key),
  setItem: () => {
    throw new DOMException("full", "QuotaExceededError");
  },
} as unknown as Storage;

/** Replies in turn, then saves for good. */
function replies(...queued: (() => Response | Promise<Response>)[]) {
  return () => (queued.shift() ?? saved)();
}

const states = (outbox: Outbox) => outbox.entries.value.map((e) => e.state);
const stored = () => JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
const bodies = (sent: Request[]) =>
  Promise.all(sent.map((request) => request.clone().json()));

describe("queueing", () => {
  test("a batch gets a key, is kept, sent at once, and settles as saved", async () => {
    const { api, sent } = answering(() => saved(41));
    const outbox = new Outbox(api);
    expect(outbox.queue(draft(), "Move: 1 item")).toBe(true);
    await vi.waitFor(() => expect(states(outbox)).toEqual(["saved"]));
    const [kept] = outbox.entries.value;
    expect(kept).toMatchObject({ transactionId: 41, what: "Move: 1 item" });
    expect(kept?.body.key).toMatch(/^[0-9a-f-]{36}$/);
    expect(await bodies(sent)).toEqual([kept?.body]);
    expect(new URL(sent[0]?.url ?? "").pathname).toBe("/api/transactions");
    expect(stored()).toEqual(outbox.entries.value);
  });

  test("each batch queued gets a key of its own", () => {
    const { api } = answering(() => json({ reason: "unregistered" }, 403));
    const outbox = new Outbox(api);
    outbox.queue(draft(), "a");
    outbox.queue(draft(), "b");
    const [first, second] = outbox.entries.value.map((e) => e.body.key);
    expect(first).not.toBe(second);
  });

  test("a browser that will not store it says so, shows and sends nothing", () => {
    const { api, sent } = answering(() => saved());
    const outbox = new Outbox(api, full);
    expect(outbox.queue(draft(), "a")).toBe(false);
    expect(outbox.entries.value).toEqual([]);
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
      outbox.queue(draft(), "a");
      await vi.waitFor(() =>
        expect(outbox.entries.value[0]?.waitingFor).toBe("network"),
      );
      expect(states(outbox)).toEqual(["waiting"]);
      await vi.advanceTimersByTimeAsync(RETRY_MS);
      await vi.waitFor(() => expect(states(outbox)).toEqual(["saved"]));
      const body = outbox.entries.value[0]?.body;
      expect(await bodies(sent)).toEqual([body, body]);
    },
  );

  test("an unregistered device waits for registering, then sends the same batch", async () => {
    const { api, sent } = answering(
      replies(() => json({ reason: "unregistered" }, 403)),
    );
    const outbox = new Outbox(api);
    outbox.queue(draft(), "a");
    await vi.waitFor(() =>
      expect(outbox.entries.value[0]?.waitingFor).toBe("registration"),
    );
    // No timer: nothing changes until the device registers.
    await vi.advanceTimersByTimeAsync(RETRY_MS * 3);
    expect(sent).toHaveLength(1);
    await outbox.send();
    expect(states(outbox)).toEqual(["saved"]);
    const [once, again] = await bodies(sent);
    expect(again).toEqual(once);
  });

  test("an unregistered device notes every waiting batch", async () => {
    const { api } = answering(() => json({ reason: "unregistered" }, 403));
    store(entry(), entry());
    const outbox = new Outbox(api);
    await outbox.send();
    expect(outbox.entries.value.map((e) => e.waitingFor)).toEqual([
      "registration",
      "registration",
    ]);
  });

  test.each([
    ["no_answer", () => Promise.reject(new TypeError("Failed to fetch"))],
    ["unavailable", () => json({ reason: "database_unavailable" }, 503)],
    ["unexpected", () => new Response("oops", { status: 500 })],
  ] as const)(
    "a batch to send again is reported as %s",
    async (reason, first) => {
      const record = vi.spyOn(telemetry, "record");
      const { api } = answering(replies(first));
      new Outbox(api).queue(draft(), "a");
      await vi.waitFor(() =>
        expect(record).toHaveBeenCalledWith({ kind: "outbox_retry", reason }),
      );
      record.mockRestore();
    },
  );

  test("a server that can't save just now says so, not that there was no answer", async () => {
    const { api } = answering(() =>
      json({ reason: "database_unavailable" }, 503),
    );
    const outbox = new Outbox(api);
    outbox.queue(draft(), "a");
    await vi.waitFor(() =>
      expect(outbox.entries.value[0]?.note).toBe(
        "The inventory can't save just now. Trying again shortly.",
      ),
    );
  });

  test("a revoked device is told to register again", async () => {
    const { api } = answering(() => json({ reason: "revoked" }, 403));
    const outbox = new Outbox(api);
    outbox.queue(draft(), "a");
    await vi.waitFor(() =>
      expect(outbox.entries.value[0]?.note).toContain("withdrawn"),
    );
  });

  test("no answer holds back the batches after it", async () => {
    const { api, sent } = answering(
      replies(() => json({ reason: "database_unavailable" }, 503)),
    );
    const outbox = new Outbox(api);
    outbox.queue(draft(), "first");
    await vi.waitFor(() =>
      expect(outbox.entries.value[0]?.waitingFor).toBe("network"),
    );
    outbox.queue(draft(), "second");
    await vi.waitFor(() => expect(states(outbox)).toEqual(["saved", "saved"]));
    const [first, second] = outbox.entries.value.map((e) => e.body.key);
    // The first goes again before the second goes at all.
    expect((await bodies(sent)).map((body) => body.key)).toEqual([
      first,
      first,
      second,
    ]);
  });
});

describe("a batch the server keeps failing on", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => vi.useRealTimers());

  test("does not hold back the batches after it", async () => {
    const { api, sent } = answering(async (request) =>
      (await request.clone().json()).volunteer_id === 9
        ? new Response("oops", { status: 500 })
        : saved(),
    );
    const outbox = new Outbox(api);
    outbox.queue(draft({ volunteer_id: 9 }), "stuck");
    outbox.queue(draft(), "next");
    await vi.waitFor(() =>
      expect(states(outbox)).toEqual(["waiting", "saved"]),
    );
    expect(outbox.entries.value[0]).toMatchObject({ failures: 1 });
    // Kept, and tried again next round.
    const before = sent.length;
    await vi.advanceTimersByTimeAsync(RETRY_MS);
    await vi.waitFor(() => expect(sent.length).toBe(before + 1));
    expect(outbox.entries.value[0]).toMatchObject({
      state: "waiting",
      failures: 2,
    });
  });

  test("asks for a report after failing again and again, and still keeps it", async () => {
    const { api } = answering(() => new Response("oops", { status: 500 }));
    const outbox = new Outbox(api);
    outbox.queue(draft(), "stuck");
    for (let round = 1; round < REPORT_AFTER; round += 1) {
      await vi.waitFor(() =>
        expect(outbox.entries.value[0]?.failures).toBe(round),
      );
      expect(outbox.entries.value[0]?.note).toContain("Trying again shortly");
      await vi.advanceTimersByTimeAsync(RETRY_MS);
    }
    await vi.waitFor(() =>
      expect(outbox.entries.value[0]?.failures).toBe(REPORT_AFTER),
    );
    expect(outbox.entries.value[0]?.note).toContain("please report this");
    expect(states(outbox)).toEqual(["waiting"]);
  });

  test("a success clears its failures", async () => {
    const { api } = answering(
      replies(() => new Response("oops", { status: 500 })),
    );
    const outbox = new Outbox(api);
    outbox.queue(draft(), "a");
    await vi.waitFor(() => expect(outbox.entries.value[0]?.failures).toBe(1));
    await vi.advanceTimersByTimeAsync(RETRY_MS);
    await vi.waitFor(() => expect(states(outbox)).toEqual(["saved"]));
    expect(outbox.entries.value[0]?.failures).toBe(0);
  });
});

describe("refusals", () => {
  test("a reused key is refused, and the next batch still goes", async () => {
    const { api } = answering(
      replies(() => json({ reason: "key_reused" }, 409)),
    );
    const outbox = new Outbox(api);
    outbox.queue(draft(), "first");
    outbox.queue(draft(), "second");
    await vi.waitFor(() =>
      expect(states(outbox)).toEqual(["refused", "saved"]),
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
    outbox.queue(draft(), "a");
    await vi.waitFor(() => expect(states(outbox)).toEqual(["refused"]));
    expect(outbox.entries.value[0]?.note).toBe(
      "Not saved. Line 2: There is no such row Value error, a move has only a from and to location",
    );
  });

  test("a settled batch can be dismissed; a waiting one cannot", () => {
    const refused = entry({ state: "refused", note: "no" });
    const waiting = entry();
    store(refused, waiting);
    const outbox = new Outbox(answering(() => saved()).api);
    outbox.dismiss(refused.body.key);
    outbox.dismiss(waiting.body.key);
    expect(outbox.entries.value).toEqual([waiting]);
  });
});

describe("storage", () => {
  test("waiting batches outlive the page", async () => {
    const waiting = entry({ waitingFor: "registration" });
    store(waiting);
    const outbox = new Outbox(answering(() => saved()).api);
    expect(outbox.entries.value).toEqual([waiting]);
    await outbox.send();
    expect(states(outbox)).toEqual(["saved"]);
  });

  test("an unreadable entry is dropped, and only that one", () => {
    const good = entry();
    store(good, { ...good, body: "not a batch" }, 7, null);
    expect(new Outbox(answering(() => saved()).api).entries.value).toEqual([
      good,
    ]);
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
      outbox.queue(draft(), `batch ${n}`);
      await vi.waitFor(() => expect(states(outbox).at(-1)).toBe("saved"));
    }
    expect(outbox.entries.value.map((e) => e.what)).toEqual(
      Array.from({ length: 10 }, (_, n) => `batch ${n + 2}`),
    );
  });

  test("another tab's batch is not lost when this tab writes", async () => {
    const { api } = answering(() => json({ reason: "unregistered" }, 403));
    const here = new Outbox(api);
    const there = new Outbox(api);
    there.queue(draft(), "there");
    here.queue(draft(), "here");
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
    outbox.queue(draft(), "a");
    await vi.waitFor(() =>
      expect(outbox.entries.value[0]?.waitingFor).toBe("network"),
    );
    window.dispatchEvent(new Event("online"));
    await vi.waitFor(() => expect(states(outbox)).toEqual(["saved"]));
    expect(sent).toHaveLength(2);
    stop();
  });

  test("another tab's change shows here", () => {
    const outbox = new Outbox(answering(() => saved()).api);
    const stop = outbox.listen();
    const other = entry({ state: "saved", transactionId: 3 });
    store(other);
    window.dispatchEvent(new StorageEvent("storage", { key: STORAGE_KEY }));
    expect(outbox.entries.value).toEqual([other]);
    stop();
  });

  test("sending twice at once sends each batch once", async () => {
    const { api, sent } = answering(() => saved());
    store(entry());
    const outbox = new Outbox(api);
    await Promise.all([outbox.send(), outbox.send()]);
    expect(sent).toHaveLength(1);
  });
});
