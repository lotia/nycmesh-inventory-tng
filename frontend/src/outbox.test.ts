import { v4 as uuid } from "uuid";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  BEFORE,
  type Draft,
  Entry,
  newEntry,
  Outbox,
  REPORT_AFTER,
  RETRY_MS,
  SAVED_MOST,
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

function storeAt(key: string, ...entries: unknown[]) {
  localStorage.setItem(key, JSON.stringify(entries));
}

const store = (...entries: unknown[]) => storeAt(STORAGE_KEY, ...entries);

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
const stored = (key = STORAGE_KEY) =>
  JSON.parse(localStorage.getItem(key) ?? "null");
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

  test("a 422 says which line of the cart, in the server's words", async () => {
    // The cart's first line went as two, a box and loose units.
    const lines = [
      { item_id: 5, quantity: "4", package_id: 9 },
      { item_id: 5, quantity: "1" },
      { item_id: 6, quantity: "2" },
    ];
    const detail = [
      {
        type: "not_found",
        loc: ["body", "lines", 2, "item_id"],
        msg: "There is no such row",
      },
      {
        type: "value_error",
        loc: ["body"],
        msg: "Value error, a move has only a from and to location",
      },
      // No words of the server's: said plainly.
      { type: "missing", loc: ["body", "lines", 0, "quantity"] },
      {
        type: "not_found",
        loc: ["body", "used_up", 0],
        msg: "There is no such row",
      },
      {
        type: "not_found",
        loc: ["body", "lengths", 0, "open_package_key"],
        msg: "There is no such row",
      },
    ];
    const { api } = answering(() => json({ detail }, 422));
    const outbox = new Outbox(api);
    outbox.queue(draft({ lines }), "a");
    await vi.waitFor(() => expect(states(outbox)).toEqual(["refused"]));
    expect(outbox.entries.value[0]?.note).toBe(
      "Not saved. Line 2: There is no such row Value error, a move has only a from and to location Line 1: Not accepted Open package said used up, 1: There is no such row What is on an open package, 1: There is no such row",
    );
  });

  test("a batch taking from an opening not yet saved waits for it", async () => {
    const key = "33333333-3333-4333-8333-333333333333";
    const opening = draft({
      kind: "open",
      to_location_id: 1,
      lines: [
        { item_id: 5, quantity: "305", package_id: 9, open_package_key: key },
        // A box its item does not follow: no key.
        { item_id: 6, quantity: "100", package_id: 8 },
      ],
    });
    const cut = draft({
      lines: [{ item_id: 5, quantity: "20", open_package_key: key }],
    });
    // The opening gets no good answer: the cut is not tried behind it.
    const { api, sent } = answering(
      replies(() => new Response("", { status: 500 })),
    );
    const outbox = new Outbox(api);
    outbox.queue(opening, "opening");
    outbox.queue(cut, "cut");
    await vi.waitFor(() => expect(sent.length).toBeGreaterThan(0));
    await outbox.send();
    expect((await bodies(sent)).map((body) => body.kind)).toEqual(["open"]);
    expect(states(outbox)).toEqual(["waiting", "waiting"]);
    await outbox.send();
    expect(states(outbox)).toEqual(["saved", "saved"]);
  });

  test("a batch naming an open package an earlier unsaved one names waits", async () => {
    const key = "33333333-3333-4333-8333-333333333333";
    const cut = draft({
      lines: [{ item_id: 5, quantity: "50", open_package_key: key }],
    });
    const whole = draft({
      lines: [
        { item_id: 5, quantity: "255", open_package_key: key, whole: true },
      ],
    });
    const { api, sent } = answering(
      replies(() => new Response("", { status: 500 })),
    );
    const outbox = new Outbox(api);
    outbox.queue(cut, "cut");
    outbox.queue(whole, "whole");
    await vi.waitFor(() => expect(sent.length).toBeGreaterThan(0));
    await outbox.send();
    // Only the cut was tried: the whole move waits behind it.
    expect((await bodies(sent)).map((body) => body.lines[0].quantity)).toEqual([
      "50",
    ]);
  });

  test("a report of what is on an open package waits for its opening", async () => {
    const key = "33333333-3333-4333-8333-333333333333";
    const opening = draft({
      kind: "open",
      to_location_id: 1,
      lines: [
        { item_id: 5, quantity: "305", package_id: 9, open_package_key: key },
      ],
    });
    const report = draft({
      kind: "report",
      to_location_id: null,
      lines: [],
      lengths: [{ open_package_key: key, left: "250" }],
    });
    const { api, sent } = answering(
      replies(() => new Response("", { status: 500 })),
    );
    const outbox = new Outbox(api);
    outbox.queue(opening, "opening");
    outbox.queue(report, "report");
    await vi.waitFor(() => expect(sent.length).toBeGreaterThan(0));
    await outbox.send();
    expect((await bodies(sent)).map((body) => body.kind)).toEqual(["open"]);
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

  test("a batch's sealed packages are kept and sent; an older page's lines too", async () => {
    // An older page's lines have no package_id: the server takes them loose.
    const lines = [
      { item_id: 5, quantity: "305", package_id: 9 },
      { item_id: 6, quantity: "3" },
    ];
    const waiting = entry({ body: { ...entry().body, lines } });
    store(waiting);
    const { api, sent } = answering(() => saved());
    const outbox = new Outbox(api);
    expect(outbox.entries.value).toEqual([waiting]);
    await outbox.send();
    expect((await bodies(sent))[0].lines).toEqual(lines);
  });

  test("an older build's batches are sent, and copied here, left there for its tabs", async () => {
    const mine = entry({ queuedAt: 2 });
    const older = entry({ queuedAt: 1 });
    store(mine);
    storeAt(BEFORE, older);
    const { api, sent } = answering(() => saved());
    const outbox = new Outbox(api);
    expect(outbox.entries.value).toEqual([older, mine]);
    await outbox.send();
    expect(sent).toHaveLength(2);
    expect(stored().map((e: Entry) => e.state)).toEqual(["saved", "saved"]);
    // An older tab still sends, settles and counts its own: the server
    // saves it once, by its key.
    expect(stored(BEFORE)).toEqual([older]);
  });

  test("a batch copied here and forgotten is not copied again", () => {
    const older = entry({ state: "saved", transactionId: 1 });
    storeAt(BEFORE, older);
    const outbox = new Outbox(answering(() => saved()).api);
    outbox.dismiss(older.body.key);
    outbox.forgetShown([older.body.key]);
    expect(outbox.entries.value).toEqual([]);
    expect(new Outbox(answering(() => saved()).api).entries.value).toEqual([]);
  });

  test("the first build's batches are read only while the second's key is absent", () => {
    const waiting = entry();
    storeAt("inventory.outbox.v1", waiting);
    expect(new Outbox(answering(() => saved()).api).entries.value).toEqual([
      waiting,
    ]);
    // Unreadable is not absent: the build before copied the first's.
    localStorage.setItem(BEFORE, "not json");
    expect(new Outbox(answering(() => saved()).api).entries.value).toEqual([]);
    // Nor is a key the browser will not read.
    localStorage.removeItem(BEFORE);
    const refusing = {
      getItem: (key: string) => {
        if (key === BEFORE) {
          throw new DOMException("denied", "SecurityError");
        }
        return localStorage.getItem(key);
      },
    } as unknown as Storage;
    expect(
      new Outbox(answering(() => saved()).api, refusing).entries.value,
    ).toEqual([]);
  });

  test("an older tab's rewrite drops nothing here: not dismissed, not a saved batch", () => {
    const saves = Array.from({ length: 12 }, (_, n) =>
      entry({ state: "saved", transactionId: n + 1, queuedAt: n }),
    );
    store(...saves);
    const outbox = new Outbox(answering(() => saved()).api);
    outbox.dismiss(saves[0]?.body.key ?? "");
    // A tab of the build before keeps 10 saved batches and drops fields it
    // cannot read from what it rewrites: only its own key.
    const theirs = entry({ queuedAt: 20 });
    storeAt(BEFORE, theirs);
    outbox.queue(draft(), "mine", 30);
    expect(stored()).toHaveLength(14);
    expect(stored()[0]).toEqual({ ...saves[0], dismissed: true });
    expect(outbox.shown().map((e) => e.body.key)).not.toContain(
      saves[0]?.body.key,
    );
  });

  test("a batch both here and in an older key is this build's", () => {
    const here = entry({ state: "saved", transactionId: 4 });
    store(here);
    storeAt(BEFORE, { ...here, state: "waiting", transactionId: null });
    expect(new Outbox(answering(() => saved()).api).entries.value).toEqual([
      here,
    ]);
  });

  test("what a batch holds is as its key says: a new field takes a new key", () => {
    // Every field path of a stored batch. Changed once a build with this
    // key is deployed, it needs a new STORAGE_KEY: a tab of the build
    // before would drop the new field from what it writes back
    // (CONTRIBUTING.md, The scan page).
    // And every value a field allows, which it would drop an entry for.
    const paths = (schema: unknown, at: string[] = []): string[] => {
      const s = schema as {
        entries?: Record<string, unknown>;
        item?: unknown;
        wrapped?: unknown;
        options?: unknown[];
        literal?: unknown;
      };
      if (s.entries !== undefined) {
        return Object.entries(s.entries).flatMap(([name, field]) => [
          [...at, name].join("."),
          ...paths(field, [...at, name]),
        ]);
      }
      if (s.literal !== undefined) {
        return [`${at.join(".")} = ${String(s.literal)}`];
      }
      if (s.options?.every((each) => typeof each === "string")) {
        return [`${at.join(".")} = ${s.options.join(" | ")}`];
      }
      const inner = s.options ?? [s.item ?? s.wrapped].filter(Boolean);
      return inner.flatMap((each) => paths(each, at));
    };
    expect({ [STORAGE_KEY]: paths(Entry).sort() }).toEqual({
      "inventory.outbox.v3": [
        "body",
        "body.from_location_id",
        "body.install_number",
        "body.key",
        "body.kind",
        "body.kind = move | receipt | install | open | report",
        "body.lengths",
        "body.lengths.left",
        "body.lengths.open_package_key",
        "body.lines",
        "body.lines.item_id",
        "body.lines.open_package_key",
        "body.lines.package_id",
        "body.lines.package_size",
        "body.lines.quantity",
        "body.lines.whole",
        "body.to_location_id",
        "body.used_up",
        "body.volunteer_id",
        "dismissed",
        "dismissed = true",
        "failures",
        "note",
        "queuedAt",
        "state",
        "state = waiting | saved | refused",
        "transactionId",
        "waitingFor",
        "waitingFor = network | registration",
        "what",
      ],
    });
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

  test("saved batches are kept until a catalogue shows them, then the ten newest", async () => {
    let id = 0;
    const { api } = answering(() => saved(++id));
    const outbox = new Outbox(api);
    for (let n = 0; n < 12; n += 1) {
      outbox.queue(draft(), `batch ${n}`);
      await vi.waitFor(() => expect(states(outbox).at(-1)).toBe("saved"));
    }
    expect(outbox.entries.value).toHaveLength(12);
    const keys = outbox.entries.value.map((e) => e.body.key);
    // A catalogue showing only the oldest: the second, unshown, stays.
    outbox.forgetShown([keys[0] ?? ""]);
    expect(outbox.entries.value.map((e) => e.what)).toEqual(
      Array.from({ length: 11 }, (_, n) => `batch ${n + 1}`),
    );
    outbox.forgetShown(keys);
    expect(outbox.entries.value.map((e) => e.what)).toEqual(
      Array.from({ length: 10 }, (_, n) => `batch ${n + 2}`),
    );
  });

  test("at most SAVED_MOST saved batches are kept, shown or not", async () => {
    store(
      ...Array.from({ length: SAVED_MOST }, (_, n) =>
        entry({ state: "saved", transactionId: n + 1, what: `batch ${n}` }),
      ),
    );
    const outbox = new Outbox(answering(() => saved()).api);
    outbox.queue(draft(), "newest");
    await vi.waitFor(() => expect(states(outbox).at(-1)).toBe("saved"));
    const kept = outbox.entries.value.map((e) => e.what);
    expect(kept).toHaveLength(SAVED_MOST);
    expect(kept[0]).toBe("batch 1");
    expect(kept.at(-1)).toBe("newest");
  });

  test("a saved batch dismissed leaves the list, and is forgotten once a catalogue shows it", () => {
    const first = entry({ state: "saved", transactionId: 1 });
    const second = entry({ state: "saved", transactionId: 2 });
    store(first, second);
    const outbox = new Outbox(answering(() => saved()).api);
    outbox.dismiss(first.body.key);
    expect(outbox.shown()).toEqual([second]);
    expect(stored()).toEqual([{ ...first, dismissed: true }, second]);
    outbox.forgetShown([second.body.key]);
    expect(outbox.entries.value).toHaveLength(2);
    outbox.forgetShown([first.body.key, second.body.key]);
    expect(outbox.entries.value).toEqual([second]);
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

  test("the page shown again sends what waits; hidden, nothing", async () => {
    const { api, sent } = answering(() => saved());
    store(entry());
    const outbox = new Outbox(api);
    const stop = outbox.listen();
    const shown = (state: DocumentVisibilityState) => {
      Object.defineProperty(document, "visibilityState", {
        value: state,
        configurable: true,
      });
      document.dispatchEvent(new Event("visibilitychange"));
    };
    shown("hidden");
    expect(sent).toHaveLength(0);
    shown("visible");
    await vi.waitFor(() => expect(states(outbox)).toEqual(["saved"]));
    Reflect.deleteProperty(document, "visibilityState");
    stop();
  });

  test("a saved batch another tab forgets stays here, off the list, until this tab's catalogue shows it", () => {
    const old = entry({ state: "saved", transactionId: 1, queuedAt: 1 });
    const kept = entry({ state: "saved", transactionId: 2, queuedAt: 2 });
    const waiting = entry({ queuedAt: 3 });
    store(old, kept, waiting);
    const outbox = new Outbox(answering(() => saved()).api);
    const stop = outbox.listen();
    store(kept, waiting);
    window.dispatchEvent(new StorageEvent("storage", { key: STORAGE_KEY }));
    expect(outbox.entries.value).toEqual([old, kept, waiting]);
    expect(outbox.shown()).toEqual([kept, waiting]);
    outbox.forgetShown([old.body.key]);
    expect(outbox.entries.value).toEqual([kept, waiting]);
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

  test("an older build's tab's batch shows here", () => {
    const outbox = new Outbox(answering(() => saved()).api);
    const stop = outbox.listen();
    const theirs = entry();
    storeAt(BEFORE, theirs);
    window.dispatchEvent(new StorageEvent("storage", { key: BEFORE }));
    expect(outbox.entries.value).toEqual([theirs]);
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
