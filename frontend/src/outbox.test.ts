import { v4 as uuid } from "uuid";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import openapi from "./api/openapi.json";
import { BUILD, Builds, HEADER, MUST_UPGRADE } from "./build";
import { say, speak } from "./i18n";
import { messages as pseudo } from "./locales/pseudo.po";
import {
  batchId,
  type Draft,
  Entry,
  KEY_REUSED,
  Kept,
  NOT_ACCEPTED,
  NOT_RECORDED,
  newEntry,
  noteOf,
  ON_LINE,
  Outbox,
  PROBLEMS,
  type ProblemKind,
  REPORT_AFTER,
  RETRY_MS,
  recordedHeading,
  SAVED_MOST,
  SOME_NOT_RECORDED,
  STORAGE_KEY,
  type Summary,
  UNAVAILABLE,
  wordsOf,
} from "./outbox";
import { telemetry } from "./telemetry";
import { answering, deferred, json, saved, TOO_OLD } from "./test-fetch";
import { refusing } from "./test-storage";

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

/** A batch's summary, told apart from others' by the place it names. */
const named = (from: string): Summary => ({ from, to: null });

/** An entry as storage might hold it. */
function entry(fields: Partial<Entry> = {}): Entry {
  return {
    ...newEntry({ ...draft(), key: uuid() }, named("a"), BUILD, 1),
    ...fields,
  };
}

/** Kept as this build keeps them, stamped by `build`. */
function storeBy(build: string, ...entries: unknown[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ build, entries }));
}

const store = (...entries: unknown[]) => storeBy(BUILD, ...entries);

/** Kept as another tab of this build keeps them: stored, and the storage
 * event a browser fires in this one, with what was stored before. */
function storeElsewhere(...entries: unknown[]) {
  const oldValue = localStorage.getItem(STORAGE_KEY);
  store(...entries);
  const newValue = localStorage.getItem(STORAGE_KEY);
  window.dispatchEvent(
    new StorageEvent("storage", { key: STORAGE_KEY, oldValue, newValue }),
  );
}

/** Kept as builds before v4 kept them: a list, under an older key. */
function storeOlder(key: string, ...entries: unknown[]) {
  localStorage.setItem(key, JSON.stringify(entries));
}

/** Storage that reads but refuses every write. */
const full = refusing("setItem");

/** Replies in turn, then saves for good. */
function replies(...queued: (() => Response | Promise<Response>)[]) {
  return () => (queued.shift() ?? saved)();
}

const states = (outbox: Outbox) => outbox.entries.value.map((e) => e.state);

/** What the page says of the first batch's reason, as plain text. */
function said(outbox: Outbox): string | null {
  const reason = outbox.entries.value[0]?.reason ?? null;
  if (reason === null) {
    return null;
  }
  const { bold, plain = [] } = noteOf(reason);
  return wordsOf([...bold, ...plain]);
}
const stored = () =>
  JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null")?.entries;
const bodies = (sent: Request[]) =>
  Promise.all(sent.map((request) => request.clone().json()));

describe("queueing", () => {
  test("a batch gets a key, is kept, sent at once, and settles as saved", async () => {
    const { api, sent } = answering(() => saved(41));
    const outbox = new Outbox(api);
    expect(outbox.queue(draft(), named("a"))).toBe("queued");
    await vi.waitFor(() => expect(states(outbox)).toEqual(["saved"]));
    const [kept] = outbox.entries.value;
    expect(kept).toMatchObject({
      transactionId: 41,
      summary: named("a"),
    });
    expect(kept?.body.key).toMatch(/^[0-9a-f-]{36}$/);
    expect(await bodies(sent)).toEqual([kept?.body]);
    expect(new URL(sent[0]?.url ?? "").pathname).toBe("/api/transactions");
    expect(stored()).toEqual(outbox.entries.value);
  });

  test("each batch queued gets a key of its own", () => {
    const { api } = answering(() => json({ reason: "unregistered" }, 403));
    const outbox = new Outbox(api);
    outbox.queue(draft(), named("a"));
    outbox.queue(draft(), named("b"));
    const [first, second] = outbox.entries.value.map((e) => e.body.key);
    expect(first).not.toBe(second);
  });

  test("a cart's key queued again with the same batch is not queued twice (inventory-tng-e2d)", async () => {
    const { api, sent } = answering(() => saved(41));
    const outbox = new Outbox(api);
    const key = uuid();
    expect(outbox.queue(draft(), named("a"), key)).toBe("queued");
    await vi.waitFor(() => expect(states(outbox)).toEqual(["saved"]));
    // The cart, emptied by Record but not kept so, recorded again.
    expect(outbox.queue(draft(), named("a"), key)).toBe("already");
    expect(states(outbox)).toEqual(["saved"]);
    expect(sent).toHaveLength(1);
  });

  test("a cart's key queued again with another batch is not recorded, nor ever answered as the first (inventory-tng-e2d)", async () => {
    const answer = deferred();
    const { api, sent } = answering(
      replies(
        () => answer.promise,
        () => json({ reason: "key_reused" }, 409),
      ),
    );
    const outbox = new Outbox(api);
    const key = uuid();
    outbox.queue(draft(), named("a"), key);
    // Filled again by a tab that had not heard, and changed there.
    expect(
      outbox.queue(
        draft({ lines: [{ item_id: 6, quantity: "1" }] }),
        named("b"),
        key,
      ),
    ).toBe("refused");
    expect(outbox.entries.value[1]).toMatchObject({
      summary: named("b"),
      state: "refused",
      reason: { code: "recorded_elsewhere" },
    });
    answer.resolve(saved(41));
    await vi.waitFor(() =>
      expect(states(outbox)).toEqual(["saved", "refused"]),
    );
    // Asked about under the key once the first was answered (send): the
    // server refuses it (inventory-tng-0gg).
    await vi.waitFor(() => expect(sent).toHaveLength(2));
    expect(outbox.entries.value[1]?.transactionId).toBeNull();
    // Under a key of its own, so the list names each apart: dismissing it
    // leaves the first.
    const own = outbox.entries.value[1]?.body.key ?? "";
    expect(own).not.toBe(key);
    outbox.dismiss(outbox.entries.value[1] as Entry);
    expect(outbox.entries.value).toMatchObject([
      { summary: named("a"), state: "saved" },
    ]);
    expect(outbox.entries.value[0]).not.toHaveProperty("dismissed");
  });

  test("a batch just queued is named as storage reads it back, whatever its fields' order (inventory-tng-suo)", () => {
    const { api } = answering(() => new Promise<Response>(() => {}));
    const outbox = new Outbox(api);
    const key = uuid();
    outbox.queue(draft(), named("a"), key);
    // Refused at once, listed under a key of its own, never read back yet.
    outbox.queue(
      draft({ lines: [{ item_id: 6, quantity: "1" }] }),
      named("b"),
      key,
    );
    const shown = outbox.entries.value.map((each) => batchId(each.body));
    const readBack = new Outbox(api).entries.value.map((each) =>
      batchId(each.body),
    );
    expect(shown).toEqual(readBack);
  });

  test("an answer settles the batch it answers, not another a build before kept under its key (inventory-tng-e2d)", async () => {
    // A tab of a build before, which had not heard the cart was recorded,
    // queues it again under its key, changed: two batches under one key.
    const key = uuid();
    const first = entry({ body: { ...draft(), key }, putBack: true });
    const changed = entry({
      body: { ...draft({ lines: [{ item_id: 6, quantity: "1" }] }), key },
      summary: named("b"),
      putBack: true,
    });
    store(first, changed);
    const { api, sent } = answering(
      replies(
        () => saved(41),
        () => json({ reason: "key_reused" }, 409),
      ),
    );
    const outbox = new Outbox(api);
    await outbox.send();
    expect(sent).toHaveLength(2);
    expect(outbox.entries.value).toMatchObject([
      { state: "saved", transactionId: 41 },
      {
        summary: named("b"),
        state: "refused",
        reason: { code: "recorded_elsewhere" },
      },
    ]);
  });

  test("of batches under one key, one dismissed or with its difference added is that one alone (inventory-tng-suo)", () => {
    // Saved, then queued again under its key, changed, by tabs of an older
    // build, and refused as the key was used.
    const key = uuid();
    const changed = (quantity: string, what: string): Entry =>
      entry({
        body: { ...draft({ lines: [{ item_id: 6, quantity }] }), key },
        summary: named(what),
        state: "refused",
        reason: { code: "recorded_elsewhere" },
        putBack: true,
      });
    const first = entry({
      body: { ...draft(), key },
      state: "saved",
      transactionId: 41,
    });
    const second = changed("1", "b");
    const third = changed("2", "c");
    store(first, second, third);
    const outbox = new Outbox(answering(() => saved()).api);
    expect(outbox.differenceAdded(third)).toBe(true);
    expect(outbox.entries.value.map((e) => e.reason)).toEqual([
      null,
      { code: "recorded_elsewhere" },
      { code: "recorded_elsewhere", added: true },
    ]);
    outbox.dismiss(second);
    expect(outbox.entries.value.map((e) => e.summary.from)).toEqual(["a", "c"]);
    expect(outbox.entries.value[0]).not.toHaveProperty("dismissed");
  });

  test("a cart's key whose batch was refused for what it held (422) is sent with another (inventory-tng-e2d)", async () => {
    let reply = json({ detail: [] }, 422);
    const { api, sent } = answering(() => reply);
    const outbox = new Outbox(api);
    const key = uuid();
    outbox.queue(draft(), named("a"), key);
    await vi.waitFor(() => expect(states(outbox)).toEqual(["refused"]));
    // Fixed in the cart, whose emptying was not kept, and recorded again.
    reply = saved(41);
    outbox.queue(
      draft({ lines: [{ item_id: 6, quantity: "1" }] }),
      named("b"),
      key,
    );
    await vi.waitFor(() =>
      expect(states(outbox)).toEqual(["refused", "saved"]),
    );
    const bodies = await Promise.all(sent.map((each) => each.clone().json()));
    expect(bodies.map((each) => each.key)).toEqual([key, key]);
    // The refused one, under a key of its own, is not taken as saved.
    expect(outbox.entries.value[0]?.body.key).not.toBe(key);
    expect(outbox.entries.value[0]?.reason?.code).toBe("refused");
    expect(outbox.entries.value[1]?.transactionId).toBe(41);
  });

  test("a browser that will not store it says so, shows and sends nothing", () => {
    const { api, sent } = answering(() => saved());
    const outbox = new Outbox(api, full);
    expect(outbox.queue(draft(), named("a"))).toBe("not_kept");
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
      outbox.queue(draft(), named("a"));
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
    outbox.queue(draft(), named("a"));
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

  test("an unregistered device notes every waiting batch, and only those", async () => {
    const { api } = answering(() => json({ reason: "unregistered" }, 403));
    const done = entry({ state: "saved", transactionId: 1 });
    store(done, entry(), entry());
    const outbox = new Outbox(api);
    await outbox.send();
    expect(outbox.entries.value.map((e) => e.waitingFor)).toEqual([
      done.waitingFor,
      "registration",
      "registration",
    ]);
    expect(outbox.entries.value[0]).toEqual(done);
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
      new Outbox(api).queue(draft(), named("a"));
      await vi.waitFor(() =>
        expect(record).toHaveBeenCalledWith({ kind: "outbox_retry", reason }),
      );
      record.mockRestore();
    },
  );

  test("a server that can't record just now says so, not that there was no answer", async () => {
    const { api } = answering(() =>
      json({ reason: "database_unavailable" }, 503),
    );
    const outbox = new Outbox(api);
    outbox.queue(draft(), named("a"));
    await vi.waitFor(() => expect(said(outbox)).toBe(say(UNAVAILABLE)));
  });

  test("a revoked device is told to register again", async () => {
    const { api } = answering(() => json({ reason: "revoked" }, 403));
    const outbox = new Outbox(api);
    outbox.queue(draft(), named("a"));
    await vi.waitFor(() => expect(said(outbox)).toContain("withdrawn"));
  });

  test("no answer holds back the batches after it", async () => {
    const { api, sent } = answering(
      replies(() => json({ reason: "database_unavailable" }, 503)),
    );
    const outbox = new Outbox(api);
    outbox.queue(draft(), named("first"));
    await vi.waitFor(() =>
      expect(outbox.entries.value[0]?.waitingFor).toBe("network"),
    );
    outbox.queue(draft(), named("second"));
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
    outbox.queue(draft({ volunteer_id: 9 }), named("stuck"));
    outbox.queue(draft(), named("next"));
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
    outbox.queue(draft(), named("stuck"));
    for (let round = 1; round < REPORT_AFTER; round += 1) {
      await vi.waitFor(() =>
        expect(outbox.entries.value[0]?.failures).toBe(round),
      );
      expect(said(outbox)).toContain("Trying again shortly");
      await vi.advanceTimersByTimeAsync(RETRY_MS);
    }
    await vi.waitFor(() =>
      expect(outbox.entries.value[0]?.failures).toBe(REPORT_AFTER),
    );
    expect(said(outbox)).toContain("please report this");
    expect(states(outbox)).toEqual(["waiting"]);
  });

  test("a success clears its failures", async () => {
    const { api } = answering(
      replies(() => new Response("oops", { status: 500 })),
    );
    const outbox = new Outbox(api);
    outbox.queue(draft(), named("a"));
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
    outbox.queue(draft(), named("first"));
    outbox.queue(draft(), named("second"));
    await vi.waitFor(() =>
      expect(states(outbox)).toEqual(["refused", "saved"]),
    );
    expect(said(outbox)).toBe(say(KEY_REUSED));
  });

  test("a save put back goes under its key, and refused as reused keeps that another tab recorded it first, not the page's reload words", async () => {
    const { api, sent } = answering(
      replies(() => json({ reason: "key_reused" }, 409)),
    );
    const outbox = new Outbox(api);
    const key = "0b7c6a51-9a3d-4d39-8f3e-1d1f2c4b5a61";
    outbox.queue(draft(), named("first"), key);
    await vi.waitFor(() => expect(states(outbox)).toEqual(["refused"]));
    expect(outbox.entries.value[0]).toMatchObject({
      body: { key },
      putBack: true,
      reason: { code: "recorded_elsewhere" },
    });
    expect(said(outbox)).toBe(say(SOME_NOT_RECORDED));
    expect((await bodies(sent))[0]).toMatchObject({ key });
  });

  test("a 422 says which line of the cart, and why in the page's words for its kind", async () => {
    // The cart's first line went as two, a box and loose units.
    const lines = [
      { item_id: 5, quantity: "4", package_id: 9, package_size: "4" },
      { item_id: 5, quantity: "1" },
      { item_id: 6, quantity: "2" },
    ];
    const detail = [
      { type: "not_found", loc: ["body", "lines", 2, "item_id"] },
      // FastAPI's own, its words not the page's: said plainly.
      {
        type: "value_error",
        loc: ["body"],
        msg: "Value error, a move has only a from and to location",
      },
      { type: "missing", loc: ["body", "lines", 0, "quantity"] },
      { type: "not_found", loc: ["body", "used_up", 0] },
      { type: "repeated", loc: ["body", "lengths", 0, "open_package_key"] },
    ];
    const { api } = answering(() => json({ detail }, 422));
    const outbox = new Outbox(api);
    outbox.queue(draft({ lines }), named("a"));
    await vi.waitFor(() => expect(states(outbox)).toEqual(["refused"]));
    // Kept as kinds, the words made from them on show.
    expect(
      JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "").entries[0].reason,
    ).toEqual({
      code: "refused",
      problems: [
        { names: { of: "line", n: 2 }, kind: "not_found" },
        { names: null, kind: "value_error" },
        { names: { of: "line", n: 1 }, kind: "missing" },
        { names: { of: "used_up", n: 1 }, kind: "not_found" },
        { names: { of: "lengths", n: 1 }, kind: "repeated" },
      ],
    });
    const row = say(PROBLEMS.not_found);
    expect(said(outbox)).toBe(
      `Not recorded. Line 2: ${row} Not accepted Line 1: Not accepted Open package said used up, 1: ${row} What is on an open package, 1: ${say(PROBLEMS.repeated)}`,
    );
  });

  test.each([
    ...openapi.components.schemas.BatchProblem.properties.type.enum,
    ...openapi.components.schemas.SizeProblem.properties.type.enum,
  ])(
    "a problem of kind %s is said in the page's words for it, in the language spoken as it is shown",
    async (kind) => {
      const detail = [{ type: kind, loc: ["body", "lines", 0, "quantity"] }];
      const outbox = new Outbox(answering(() => json({ detail }, 422)).api);
      outbox.queue(draft(), named("a"));
      await vi.waitFor(() => expect(states(outbox)).toEqual(["refused"]));
      const words = say(PROBLEMS[kind as ProblemKind]);
      expect(words).not.toBe(say(NOT_ACCEPTED));
      expect(said(outbox)).toBe(`Not recorded. Line 1: ${words}`);
      // Kept as its kind, it is worded anew in another language.
      speak("pseudo", pseudo);
      expect(said(outbox)).toBe(
        `${say(NOT_RECORDED)} ${say(ON_LINE, { n: 1, why: say(PROBLEMS[kind as ProblemKind]) })}`,
      );
      expect(said(outbox)).not.toContain(words);
    },
  );

  test("a problem of a kind this build has no words for is not accepted", async () => {
    const detail = [{ type: "made_up", loc: ["body", "lines", 0, "item_id"] }];
    const outbox = new Outbox(answering(() => json({ detail }, 422)).api);
    outbox.queue(draft(), named("a"));
    await vi.waitFor(() => expect(states(outbox)).toEqual(["refused"]));
    expect(said(outbox)).toBe(`Not recorded. Line 1: ${say(NOT_ACCEPTED)}`);
  });

  test("a 422 naming a report by no whole number is kept all the same", async () => {
    // Kept with a number that is not whole, the entry would no longer
    // read as one, and the next write would drop the batch.
    const detail = [{ type: "not_found", loc: ["body", "used_up", 0.5] }];
    const outbox = new Outbox(answering(() => json({ detail }, 422)).api);
    outbox.queue(draft(), named("a"));
    await vi.waitFor(() => expect(states(outbox)).toEqual(["refused"]));
    expect(
      new Outbox(answering(() => json({}, 500)).api).entries.value,
    ).toEqual(outbox.entries.value);
    expect(said(outbox)).toBe(`Not recorded. ${say(PROBLEMS.not_found)}`);
  });

  test("a batch taking from an opening not yet saved waits for it", async () => {
    const key = "33333333-3333-4333-8333-333333333333";
    const opening = draft({
      kind: "open",
      to_location_id: 1,
      lines: [
        {
          item_id: 5,
          quantity: "305",
          package_id: 9,
          package_size: "305",
          open_package_key: key,
        },
        // A box its item does not follow: no key.
        { item_id: 6, quantity: "100", package_id: 8, package_size: "100" },
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
    outbox.queue(opening, named("opening"));
    outbox.queue(cut, named("cut"));
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
    outbox.queue(cut, named("cut"));
    outbox.queue(whole, named("whole"));
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
        {
          item_id: 5,
          quantity: "305",
          package_id: 9,
          package_size: "305",
          open_package_key: key,
        },
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
    outbox.queue(opening, named("opening"));
    outbox.queue(report, named("report"));
    await vi.waitFor(() => expect(sent.length).toBeGreaterThan(0));
    await outbox.send();
    expect((await bodies(sent)).map((body) => body.kind)).toEqual(["open"]);
  });

  test("a settled batch can be dismissed; a waiting one cannot", () => {
    const refused = entry({ state: "refused", reason: { code: "key_reused" } });
    const waiting = entry();
    store(refused, waiting);
    const outbox = new Outbox(answering(() => saved()).api);
    outbox.dismiss(refused);
    outbox.dismiss(waiting);
    expect(outbox.entries.value).toEqual([waiting]);
  });
});

describe("what another tab recorded (inventory-tng-0gg)", () => {
  /** The server's 409 for a key whose transaction 41 held `lines`. */
  const recordedReply = (lines = [{ item_id: 5, quantity: "3.000" }]) =>
    json(
      {
        reason: "key_reused",
        recorded: {
          transaction_id: 41,
          kind: "move",
          volunteer_id: 1,
          from_location_id: 1,
          to_location_id: 2,
          install_number: null,
          lines: lines.map((line) => ({
            package_id: null,
            package_size: null,
            open_package_key: null,
            whole: null,
            ...line,
          })),
          used_up: [],
          lengths: [],
        },
      },
      409,
    );

  const changed = () => draft({ lines: [{ item_id: 5, quantity: "4" }] });

  test("a cart's batch refused for its key keeps what the server says was recorded", async () => {
    const { api } = answering(() => recordedReply());
    const outbox = new Outbox(api);
    const key = uuid();
    outbox.queue(changed(), named("b"), key);
    await vi.waitFor(() => expect(states(outbox)).toEqual(["refused"]));
    expect(outbox.entries.value[0]?.reason).toEqual({
      code: "recorded_elsewhere",
      recorded: {
        by: "elsewhere",
        transactionId: 41,
        batch: {
          key,
          kind: "move",
          volunteer_id: 1,
          from_location_id: 1,
          to_location_id: 2,
          install_number: null,
          lines: [
            {
              item_id: 5,
              quantity: "3.000",
              package_id: null,
              package_size: null,
              open_package_key: null,
              whole: null,
            },
          ],
          used_up: [],
          lengths: [],
        },
      },
    });
    // Then it says only that it was not recorded: the list shows the rest.
    expect(said(outbox)).toBe(say(SOME_NOT_RECORDED));
  });

  test("what was recorded is headed by who recorded it: this tab, or another (Ali, inventory-tng-0gg, 2026-11-15, 1 and 6)", () => {
    const batch = { ...draft(), key: uuid() };
    expect(recordedHeading({ by: "here", transactionId: 412, batch })).toBe(
      "Already recorded from this tab in transaction 412",
    );
    expect(
      recordedHeading({ by: "elsewhere", transactionId: 412, batch }),
    ).toBe("Recorded in another tab in transaction 412");
  });

  test("a keyless batch refused for its key still asks for a report", async () => {
    const { api } = answering(() => recordedReply());
    const outbox = new Outbox(api);
    outbox.queue(changed(), named("b"));
    await vi.waitFor(() => expect(states(outbox)).toEqual(["refused"]));
    expect(said(outbox)).toBe(say(KEY_REUSED));
  });

  /** A cart recorded here (41), then queued again changed, under its key:
   * listed refused, then asked about under the key, answered `reply`. */
  async function askedAbout(reply: () => Response | Promise<Response>) {
    const { api, sent } = answering(replies(() => saved(41), reply));
    const outbox = new Outbox(api);
    const key = uuid();
    outbox.queue(draft(), named("a"), key);
    await vi.waitFor(() => expect(states(outbox)).toEqual(["saved"]));
    expect(outbox.queue(changed(), named("b"), key)).toBe("refused");
    return { outbox, sent, key };
  }

  /** The changed batch, as listed. */
  const second = (outbox: Outbox) => outbox.entries.value[1];

  test("one listed recorded elsewhere unsent is asked about under the key, and shows what this browser recorded", async () => {
    const { outbox, sent, key } = await askedAbout(() => recordedReply());
    // Until the server says, nothing to scan again.
    expect(noteOf(second(outbox)?.reason ?? { code: "no_answer" })).toEqual({
      bold: [say(SOME_NOT_RECORDED)],
    });
    await vi.waitFor(() =>
      expect(second(outbox)?.reason).toHaveProperty("recorded"),
    );
    const asked = await (sent[1] as Request).clone().json();
    expect(asked.key).toBe(key);
    // Changed from what was recorded here: what it took off is the
    // volunteer's, for the server to keep (inventory-tng-0gg review).
    expect(asked.changed_from).toEqual(draft().lines);
    expect(second(outbox)).toMatchObject({
      state: "refused",
      transactionId: null,
      reason: { code: "recorded_elsewhere", recorded: { by: "here" } },
    });
    expect(second(outbox)?.reason).not.toHaveProperty("heldKey");
    // Under a key of its own still.
    expect(second(outbox)?.body.key).not.toBe(key);
    // Asked once.
    await outbox.send();
    expect(sent).toHaveLength(2);
  });

  test.each([
    ["before it is asked about", () => json({}, 500)],
    ["once the server said what it recorded", () => recordedReply()],
  ])(
    "the same cart recorded again, listed recorded elsewhere %s, is listed once (inventory-tng-3iy review)",
    async (_, reply) => {
      const { outbox, sent, key } = await askedAbout(reply);
      await vi.waitFor(() => expect(sent).toHaveLength(2));
      // Its cart not emptied, as the browser would not keep that, and
      // recorded again: two listed would put their difference in the cart
      // twice, each under a key of its own, to be recorded twice.
      expect(outbox.queue(changed(), named("b"), key)).toBe("already");
      expect(states(outbox)).toEqual(["saved", "refused"]);
    },
  );

  test("the same cart recorded again, sent under its key and refused as recorded elsewhere, is listed once (inventory-tng-3iy review)", async () => {
    const { api } = answering(() => json({ reason: "key_reused" }, 409));
    const outbox = new Outbox(api);
    const key = uuid();
    outbox.queue(draft(), named("a"), key);
    await vi.waitFor(() => expect(states(outbox)).toEqual(["refused"]));
    // Another cart under a key of its own is no such one.
    expect(outbox.queue(draft(), named("b"), uuid())).toBe("queued");
    expect(outbox.queue(draft(), named("a"), key)).toBe("already");
    expect(outbox.entries.value).toHaveLength(2);
  });

  test("one asked about that the server records, as nothing held the key, is saved under it", async () => {
    const { outbox, key } = await askedAbout(() => saved(42));
    await vi.waitFor(() => expect(second(outbox)?.state).toBe("saved"));
    expect(second(outbox)).toMatchObject({
      transactionId: 42,
      reason: null,
      body: { key },
    });
  });

  test("one asked about that the server records, after the batch under the key was refused for what it held, leaves that one under a key of its own", async () => {
    const { api } = answering(
      replies(
        () =>
          json(
            { detail: [{ loc: ["body", "lines", 0], type: "x", msg: "No" }] },
            422,
          ),
        () => saved(42),
      ),
    );
    const outbox = new Outbox(api);
    const key = uuid();
    outbox.queue(draft(), named("a"), key);
    // Queued while the first waits: listed refused, to be asked about.
    expect(outbox.queue(changed(), named("b"), key)).toBe("refused");
    await vi.waitFor(() => expect(second(outbox)?.state).toBe("saved"));
    const [first, recorded] = outbox.entries.value;
    expect(first?.reason?.code).toBe("refused");
    expect(recorded?.body.key).toBe(key);
    // One key names one batch.
    expect(first?.body.key).not.toBe(key);
  });

  test("the same batch queued again is not queued twice, whatever it was changed from", async () => {
    const { api, sent } = answering(() => saved(41));
    const outbox = new Outbox(api);
    const key = uuid();
    outbox.queue(
      draft({ changed_from: [{ item_id: 5, quantity: "9" }] }),
      named("a"),
      key,
    );
    await vi.waitFor(() => expect(states(outbox)).toEqual(["saved"]));
    expect(outbox.queue(draft(), named("a"), key)).toBe("already");
    expect(sent).toHaveLength(1);
  });

  test("one asked about that is the same as recorded is dropped", async () => {
    const { outbox } = await askedAbout(() =>
      json({ transaction_id: 41 }, 200),
    );
    await vi.waitFor(() => expect(outbox.entries.value).toHaveLength(1));
    expect(states(outbox)).toEqual(["saved"]);
  });

  test("one asked about the server refuses for what it holds says why", async () => {
    const { outbox } = await askedAbout(() =>
      json(
        { detail: [{ loc: ["body", "lines", 0], type: "x", msg: "No" }] },
        422,
      ),
    );
    await vi.waitFor(() =>
      expect(second(outbox)?.reason?.code).toBe("refused"),
    );
    expect(second(outbox)?.state).toBe("refused");
  });

  test("one asked about without an answer stays as it is, and is asked again", async () => {
    const { outbox, sent } = await askedAbout(
      () => new Promise<Response>((_, fail) => fail(new TypeError("offline"))),
    );
    await vi.waitFor(() => expect(sent).toHaveLength(2));
    await vi.waitFor(() =>
      expect(second(outbox)?.reason).toMatchObject({
        code: "recorded_elsewhere",
        heldKey: expect.any(String),
      }),
    );
    expect(second(outbox)?.state).toBe("refused");
  });

  test("one asked about is not answered as too old: this tab is", async () => {
    const { outbox } = await askedAbout(() => json(TOO_OLD, 409));
    await vi.waitFor(() => expect(outbox.builds.newer.value).toBe("too_old"));
    expect(second(outbox)).toMatchObject({
      state: "refused",
      reason: { code: "recorded_elsewhere", heldKey: expect.any(String) },
    });
  });

  test("one asked about from an older build's tab, too old, asks no more", async () => {
    const key = uuid();
    const held = entry({
      body: { ...changed(), key: uuid() },
      build: "older",
      state: "refused",
      reason: { code: "recorded_elsewhere", heldKey: key },
      putBack: true,
    });
    store(held);
    const { api, sent } = answering(() => json(TOO_OLD, 409));
    const outbox = new Outbox(api, localStorage, new Builds("newer"));
    await outbox.send();
    expect(sent).toHaveLength(1);
    expect(outbox.entries.value[0]?.reason).toEqual({
      code: "recorded_elsewhere",
    });
    await outbox.send();
    expect(sent).toHaveLength(1);
  });

  test("its difference put in the cart is kept so, of that one alone", async () => {
    const { api } = answering(
      replies(
        () => saved(40),
        () => recordedReply(),
      ),
    );
    const outbox = new Outbox(api);
    const key = uuid();
    outbox.queue(draft(), named("a"));
    await vi.waitFor(() => expect(states(outbox)).toEqual(["saved"]));
    outbox.queue(changed(), named("b"), key);
    await vi.waitFor(() =>
      expect(states(outbox)).toEqual(["saved", "refused"]),
    );
    expect(outbox.differenceAdded(outbox.entries.value[1] as Entry)).toBe(true);
    expect(outbox.entries.value[1]?.reason).toMatchObject({ added: true });
    expect(outbox.entries.value[0]?.reason).toBeNull();
  });

  test("one listed recorded elsewhere waits to be asked about while the batch under its key waits", async () => {
    const { api, sent } = answering(() => json({}, 500));
    const outbox = new Outbox(api);
    const key = uuid();
    outbox.queue(draft(), named("a"), key);
    await vi.waitFor(() => expect(sent).toHaveLength(1));
    expect(outbox.queue(changed(), named("b"), key)).toBe("refused");
    await outbox.send();
    const bodies = await Promise.all(sent.map((each) => each.clone().json()));
    expect(bodies.map((body) => body.lines[0].quantity)).toEqual(
      bodies.map(() => "3"),
    );
  });

  test.each([
    ["unregistered", () => json({ reason: "unregistered" }, 403)],
    ["failing", () => json({}, 500)],
  ])("one asked about, the device %s, stays as it is", async (_, reply) => {
    const { outbox, sent } = await askedAbout(reply);
    await vi.waitFor(() => expect(sent).toHaveLength(2));
    await outbox.send();
    expect(second(outbox)).toMatchObject({
      state: "refused",
      reason: { code: "recorded_elsewhere", heldKey: expect.any(String) },
    });
  });
});

describe("builds", () => {
  const MINE = "2026-11-12T10:00:00.000Z";
  const NEWER = "2026-11-12T11:00:00.000Z";
  const OLDER = "2026-11-12T09:00:00.000Z";

  /** An outbox of the build MINE, whose server answers with `reply`. */
  function of(reply: (request: Request) => Response | Promise<Response>) {
    const { api, sent } = answering(reply);
    return { outbox: new Outbox(api, localStorage, new Builds(MINE)), sent };
  }

  /** A save, from a server whose pages are `build`. */
  const savedBy = (build: string) => () => {
    const response = saved();
    response.headers.set(HEADER, build);
    return response;
  };

  test("each batch names this build", async () => {
    const { outbox, sent } = of(() => saved());
    outbox.queue(draft(), named("a"));
    await vi.waitFor(() => expect(states(outbox)).toEqual(["saved"]));
    expect(sent[0]?.headers.get(HEADER)).toBe(MINE);
  });

  test("a reply naming a newer build says one is out, and sending goes on", async () => {
    const record = vi.spyOn(telemetry, "record");
    const { outbox } = of(savedBy(NEWER));
    outbox.queue(draft(), named("a"));
    outbox.queue(draft(), named("b"));
    await vi.waitFor(() => expect(states(outbox)).toEqual(["saved", "saved"]));
    expect(outbox.builds.newer.value).toBe("server");
    expect(record).toHaveBeenCalledWith({
      kind: "out_of_date",
      learned: "server",
    });
    expect(
      record.mock.calls.filter(([event]) => event.kind === "out_of_date"),
    ).toHaveLength(1);
    record.mockRestore();
  });

  test.each([
    ["this build", MINE],
    ["an older build", "2026-11-12T09:00:00.000Z"],
    ["no build at all", "not a build"],
  ])("a reply naming %s says nothing", async (_name, build) => {
    const { outbox } = of(savedBy(build));
    outbox.queue(draft(), named("a"));
    await vi.waitFor(() => expect(states(outbox)).toEqual(["saved"]));
    expect(outbox.builds.newer.value).toBeNull();
  });

  // Only a page older than the server reads this reply, so a later server
  // may change all of it but the reason (replies.BuildTooOldReply).
  test.each([
    ["as this build's server sends it", TOO_OLD],
    ["with only its reason", { reason: "build_too_old" }],
    [
      "with its other fields renamed, and one added",
      {
        reason: "build_too_old",
        sent_build: TOO_OLD.build,
        oldest_build: 20261112,
        what_to_do: null,
        added: { anything: [1] },
      },
    ],
  ])(
    "a batch from a build too old, %s, is kept waiting, not refused, and nothing more is sent",
    async (_name, body) => {
      const { outbox, sent } = of(() => json(body, 409));
      outbox.queue(draft(), named("first"));
      outbox.queue(draft(), named("second"));
      await vi.waitFor(() => expect(outbox.builds.newer.value).toBe("too_old"));
      expect(states(outbox)).toEqual(["waiting", "waiting"]);
      expect(said(outbox)).toBe(say(MUST_UPGRADE));
      expect(stored().map((each: Entry) => each.state)).toEqual([
        "waiting",
        "waiting",
      ]);
      // Neither a dismissal nor another round sends or drops it.
      outbox.dismiss(outbox.entries.value[0] as Entry);
      await outbox.send();
      expect(sent).toHaveLength(1);
      expect(states(outbox)).toEqual(["waiting", "waiting"]);
    },
  );

  test("each batch names the build that queued it, which may be older", async () => {
    const { outbox, sent } = of(() => saved());
    store(entry({ build: OLDER }));
    outbox.queue(draft(), named("mine"));
    await vi.waitFor(() => expect(states(outbox)).toEqual(["saved", "saved"]));
    expect(sent.map((each) => each.headers.get(HEADER))).toEqual([OLDER, MINE]);
  });

  test("an older build's batch held back as too old is too old itself, not this tab", async () => {
    store(entry({ build: OLDER }));
    // A server whose oldest build is after OLDER.
    const { outbox, sent } = of((request) =>
      request.headers.get(HEADER) === OLDER ? json(TOO_OLD, 409) : saved(),
    );
    outbox.queue(draft(), named("mine"));
    await vi.waitFor(() =>
      expect(states(outbox)).toEqual(["too_old", "saved"]),
    );
    expect(outbox.builds.newer.value).toBeNull();
    expect(outbox.entries.value[0]).toMatchObject({
      waitingFor: null,
      reason: null,
    });
    // Listed with what older builds left (older.ts), not here, and never
    // sent again.
    expect(outbox.shown().map((each) => each.summary.from)).toEqual(["mine"]);
    await outbox.send();
    expect(sent).toHaveLength(2);
    expect(stored()[0].state).toBe("too_old");
  });

  test.each([
    ["held back as too old", () => json(TOO_OLD, 409)],
    ["refused", () => json({ detail: [] }, 422)],
    [
      "not answered",
      (): Promise<Response> => Promise.reject(new TypeError("Failed to fetch")),
    ],
  ])(
    "a batch another tab saved meanwhile stays saved, here %s",
    async (_name, reply) => {
      const theirs = entry({ build: OLDER });
      store(theirs);
      const { outbox } = of(() => {
        // Saved from another tab while this one's request was out.
        store({ ...theirs, state: "saved", transactionId: 7 });
        return reply();
      });
      await outbox.send();
      expect(stored()).toEqual([
        { ...theirs, state: "saved", transactionId: 7 },
      ]);
    },
  );

  test("a save another tab held back meanwhile is saved here", async () => {
    const theirs = entry({ build: OLDER });
    store(theirs);
    const { outbox } = of(() => {
      store({ ...theirs, state: "too_old" });
      return saved(8);
    });
    await outbox.send();
    expect(stored()[0]).toMatchObject({ state: "saved", transactionId: 8 });
  });

  test("a newer build's batch held back as too old says this tab is too", async () => {
    store(entry({ build: NEWER }));
    const { outbox } = of(() => json(TOO_OLD, 409));
    await outbox.send();
    expect(outbox.builds.newer.value).toBe("too_old");
    expect(states(outbox)).toEqual(["waiting"]);
  });

  // Checked by its reason alone, but by its reason: any other 409 is one
  // this build does not understand, kept and tried again.
  test.each([
    ["a reason this build does not know", { reason: "frozen", build: MINE }],
    ["no reason", { build: MINE, oldest: NEWER, message: "Load again." }],
  ])("a 409 with %s is unexpected, not too old", async (_name, body) => {
    const { outbox } = of(() => json(body, 409));
    outbox.queue(draft(), named("a"));
    await vi.waitFor(() => expect(outbox.entries.value[0]?.failures).toBe(1));
    expect(said(outbox)).toContain("answered unexpectedly");
    expect(states(outbox)).toEqual(["waiting"]);
    expect(outbox.builds.newer.value).toBeNull();
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

  test("a batch's sealed packages are kept and sent with their size; an older page's lines too", async () => {
    // An older page's lines have no package_id: the server takes them loose.
    const lines = [
      { item_id: 5, quantity: "305", package_id: 9, package_size: "305" },
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

  test("a stored line of a package without its size is read, sent, and refused naming its line", async () => {
    // Kept by a build from before sizes were sent (inventory-tng-1p8): the
    // server refuses it, and the volunteer is told which line, not left
    // with a batch dropped unseen.
    const lines = [{ item_id: 5, quantity: "305", package_id: 9 }];
    store(entry({ body: { ...entry().body, lines } }));
    const detail = [
      {
        type: "package_size_required",
        loc: ["body", "lines", 0, "package_size"],
        msg: "package_size is required when package_id is given: the size, in the item's unit, the package was counted in, from the catalogue",
      },
    ];
    const { api, sent } = answering(() => json({ detail }, 422));
    const outbox = new Outbox(api);
    await outbox.send();
    expect((await bodies(sent))[0].lines).toEqual(lines);
    expect(states(outbox)).toEqual(["refused"]);
    expect(said(outbox)).toBe(
      "Not recorded. Line 1: A line of a package says the size it was counted in: load the page again, and count it again",
    );
  });

  test("a loose line's size, refused, is told in the page's words", async () => {
    const detail = [
      {
        type: "package_size_without_package",
        loc: ["body", "lines", 0, "package_size"],
        msg: "package_size is only allowed with package_id",
      },
    ];
    store(entry());
    const outbox = new Outbox(answering(() => json({ detail }, 422)).api);
    await outbox.send();
    expect(said(outbox)).toBe(
      "Not recorded. Line 1: A loose line says a package's size: load the page again, and count it again",
    );
  });

  test("another refusal of a line's package_size is worded by its kind", async () => {
    const detail = [
      { type: "package_resized", loc: ["body", "lines", 0, "package_size"] },
    ];
    store(entry());
    const outbox = new Outbox(answering(() => json({ detail }, 422)).api);
    await outbox.send();
    expect(said(outbox)).toBe(
      `Not recorded. Line 1: ${say(PROBLEMS.package_resized)}`,
    );
  });

  test("an older build's keys are neither read, sent, nor written", async () => {
    const older = entry();
    storeOlder("inventory.outbox.v3", older);
    storeOlder("inventory.outbox.v1", entry({ state: "saved" }));
    const keys = ["inventory.outbox.v3", "inventory.outbox.v1"];
    const before = keys.map((key) => localStorage.getItem(key));
    const { api, sent } = answering(() => saved());
    const outbox = new Outbox(api);
    expect(outbox.entries.value).toEqual([]);
    outbox.queue(draft(), named("mine"));
    await vi.waitFor(() => expect(states(outbox)).toEqual(["saved"]));
    expect(sent).toHaveLength(1);
    expect(keys.map((key) => localStorage.getItem(key))).toEqual(before);
  });

  test("what is kept is stamped with the build that wrote it", () => {
    const outbox = new Outbox(
      answering(() => json({ reason: "unregistered" }, 403)).api,
      localStorage,
      new Builds("2026-11-12T10:00:00.000Z"),
    );
    outbox.queue(draft(), named("a"));
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "")).toEqual({
      build: "2026-11-12T10:00:00.000Z",
      entries: outbox.entries.value,
    });
  });

  test("this key, written by a newer build of the same key, is read", () => {
    const theirs = entry();
    storeBy("2999-01-01T00:00:00.000Z", theirs);
    expect(new Outbox(answering(() => saved()).api).entries.value).toEqual([
      theirs,
    ]);
  });

  test("what a batch holds is as its key says: a new field takes a new key", () => {
    // Every field path of a stored batch, and every value a field allows.
    // Changed once a build with this key is deployed, it needs a new
    // STORAGE_KEY: a tab of the build before would drop a field it does not
    // know from what it writes back, and an entry with a value it does not
    // allow (CONTRIBUTING.md, The scan page).
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
    // Optional fields added since a build keeping this key was deployed
    // whose loss leaves an entry as that build itself would have kept it:
    // only such a field may be added here without a new key. None yet.
    const droppedAsBefore: string[] = [];
    expect(Object.keys(Kept.entries)).toEqual(["build", "entries"]);
    expect({
      [STORAGE_KEY]: paths(Entry)
        .filter((path) => !droppedAsBefore.includes(path))
        .sort(),
    }).toEqual({
      "inventory.outbox.v5": [
        "body",
        "body.changed_from",
        "body.changed_from.item_id",
        "body.changed_from.open_package_key",
        "body.changed_from.package_id",
        "body.changed_from.package_size",
        "body.changed_from.quantity",
        "body.changed_from.whole",
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
        "build",
        "dismissed",
        "dismissed = true",
        "failures",
        "putBack",
        "putBack = true",
        "queuedAt",
        "reason",
        "reason.added",
        "reason.added = true",
        "reason.code",
        "reason.code",
        "reason.code",
        "reason.code",
        "reason.code",
        // The same values as shipped, recorded_elsewhere an object of its own.
        "reason.code = no_answer | unavailable | must_upgrade | key_reused",
        "reason.code = not_registered",
        "reason.code = recorded_elsewhere",
        "reason.code = refused",
        "reason.code = unexpected",
        "reason.heldKey",
        "reason.problems",
        "reason.problems.kind",
        "reason.problems.names",
        "reason.problems.names.n",
        "reason.problems.names.of",
        "reason.problems.names.of = line | used_up | lengths",
        "reason.recorded",
        "reason.recorded.batch",
        "reason.recorded.batch.changed_from",
        "reason.recorded.batch.changed_from.item_id",
        "reason.recorded.batch.changed_from.open_package_key",
        "reason.recorded.batch.changed_from.package_id",
        "reason.recorded.batch.changed_from.package_size",
        "reason.recorded.batch.changed_from.quantity",
        "reason.recorded.batch.changed_from.whole",
        "reason.recorded.batch.from_location_id",
        "reason.recorded.batch.install_number",
        "reason.recorded.batch.key",
        "reason.recorded.batch.kind",
        "reason.recorded.batch.kind = move | receipt | install | open | report",
        "reason.recorded.batch.lengths",
        "reason.recorded.batch.lengths.left",
        "reason.recorded.batch.lengths.open_package_key",
        "reason.recorded.batch.lines",
        "reason.recorded.batch.lines.item_id",
        "reason.recorded.batch.lines.open_package_key",
        "reason.recorded.batch.lines.package_id",
        "reason.recorded.batch.lines.package_size",
        "reason.recorded.batch.lines.quantity",
        "reason.recorded.batch.lines.whole",
        "reason.recorded.batch.to_location_id",
        "reason.recorded.batch.used_up",
        "reason.recorded.batch.volunteer_id",
        "reason.recorded.by",
        "reason.recorded.by = here | elsewhere",
        "reason.recorded.transactionId",
        "reason.status",
        "reason.tries",
        "reason.why",
        "reason.why = unregistered | revoked",
        "state",
        "state = waiting | saved | refused | too_old",
        "summary",
        "summary.from",
        "summary.to",
        "transactionId",
        "waitingFor",
        "waitingFor = network | registration",
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

  test.each(["not json", '{"an": "object"}', "[]"])(
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
      outbox.queue(draft(), named(`batch ${n}`));
      await vi.waitFor(() => expect(states(outbox).at(-1)).toBe("saved"));
    }
    expect(outbox.entries.value).toHaveLength(12);
    const keys = outbox.entries.value.map((e) => e.body.key);
    // A catalogue showing only the oldest: the second, unshown, stays.
    outbox.forgetShown([keys[0] ?? ""]);
    expect(outbox.entries.value.map((e) => e.summary.from)).toEqual(
      Array.from({ length: 11 }, (_, n) => `batch ${n + 1}`),
    );
    outbox.forgetShown(keys);
    expect(outbox.entries.value.map((e) => e.summary.from)).toEqual(
      Array.from({ length: 10 }, (_, n) => `batch ${n + 2}`),
    );
  });

  test("at most SAVED_MOST saved batches are kept, shown or not", async () => {
    store(
      ...Array.from({ length: SAVED_MOST }, (_, n) =>
        entry({
          state: "saved",
          transactionId: n + 1,
          summary: named(`batch ${n}`),
        }),
      ),
    );
    const outbox = new Outbox(answering(() => saved()).api);
    outbox.queue(draft(), named("newest"));
    await vi.waitFor(() => expect(states(outbox).at(-1)).toBe("saved"));
    const kept = outbox.entries.value.map((e) => e.summary.from);
    expect(kept).toHaveLength(SAVED_MOST);
    expect(kept[0]).toBe("batch 1");
    expect(kept.at(-1)).toBe("newest");
  });

  test("a saved batch dismissed leaves the list, and is forgotten once a catalogue shows it", () => {
    const first = entry({ state: "saved", transactionId: 1 });
    const second = entry({ state: "saved", transactionId: 2 });
    store(first, second);
    const outbox = new Outbox(answering(() => saved()).api);
    outbox.dismiss(first);
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
    there.queue(draft(), named("there"));
    here.queue(draft(), named("here"));
    await vi.waitFor(() => expect(stored()).toHaveLength(2));
    expect(here.entries.value.map((e) => e.summary.from)).toEqual([
      "there",
      "here",
    ]);
  });
});

describe("listening", () => {
  test("coming back online sends what waits", async () => {
    const { api, sent } = answering(
      replies(() => Promise.reject(new TypeError("Failed to fetch"))),
    );
    const outbox = new Outbox(api);
    const stop = outbox.listen();
    outbox.queue(draft(), named("a"));
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
    outbox.forgetShown([kept.body.key]);
    expect(outbox.entries.value).toEqual([old, kept, waiting]);
    outbox.forgetShown([old.body.key]);
    expect(outbox.entries.value).toEqual([kept, waiting]);
    stop();
  });

  /** A saved batch, and a refused one an older build's tab queued again
   * under its key (inventory-tng-0p8). */
  function underOneKey() {
    const saved = entry({ state: "saved", transactionId: 1, queuedAt: 1 });
    const refused = entry({
      body: { ...saved.body, lines: [{ item_id: 6, quantity: "1" }] },
      state: "refused",
      reason: { code: "refused", problems: [] },
      queuedAt: 2,
    });
    return { saved, refused };
  }

  test("a saved batch another tab forgets stays here, though another batch holds its key", () => {
    const { saved, refused } = underOneKey();
    store(saved, refused);
    const outbox = new Outbox(answering(() => json({}, 500)).api);
    const stop = outbox.listen();
    store(refused);
    window.dispatchEvent(new StorageEvent("storage", { key: STORAGE_KEY }));
    // Still counted (cart.ts, unseen), and its sibling still listed.
    expect(outbox.entries.value).toEqual([saved, refused]);
    expect(outbox.shown()).toEqual([refused]);
    outbox.forgetShown([saved.body.key]);
    expect(outbox.entries.value).toEqual([refused]);
    expect(outbox.shown()).toEqual([refused]);
    stop();
  });

  test("a batch another tab keeps under the key of one it forgot shows here", () => {
    const { saved, refused } = underOneKey();
    store(saved);
    const outbox = new Outbox(answering(() => json({}, 500)).api);
    const stop = outbox.listen();
    store();
    window.dispatchEvent(new StorageEvent("storage", { key: STORAGE_KEY }));
    store(refused);
    window.dispatchEvent(new StorageEvent("storage", { key: STORAGE_KEY }));
    expect(outbox.entries.value).toEqual([saved, refused]);
    expect(outbox.shown()).toEqual([refused]);
    stop();
  });

  test("a batch another tab forgot, then queued again, shows here", () => {
    const saved = entry({ state: "saved", transactionId: 1, queuedAt: 1 });
    const again = entry({ body: saved.body, queuedAt: 2 });
    store(saved);
    const outbox = new Outbox(answering(() => json({}, 500)).api);
    const stop = outbox.listen();
    store();
    window.dispatchEvent(new StorageEvent("storage", { key: STORAGE_KEY }));
    store(again);
    window.dispatchEvent(new StorageEvent("storage", { key: STORAGE_KEY }));
    expect(outbox.entries.value).toEqual([again]);
    expect(outbox.shown()).toEqual([again]);
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

  test("another tab of this build held back as too old makes this one too old", () => {
    const outbox = new Outbox(answering(() => saved()).api);
    const stop = outbox.listen();
    const held = { reason: { code: "must_upgrade" as const } };
    // An older build's tab, sharing the key, is too old, not this one.
    storeElsewhere(entry({ ...held, build: "2026-11-12T09:00:00.000Z" }));
    expect(outbox.builds.newer.value).toBeNull();
    storeElsewhere(entry(held));
    expect(outbox.builds.newer.value).toBe("too_old");
    stop();
  });

  test("a batch this tab saw waiting, then held back by another tab, makes this one too old", () => {
    const waiting = entry();
    store(waiting);
    const outbox = new Outbox(answering(() => saved()).api);
    const stop = outbox.listen();
    storeElsewhere({ ...waiting, reason: { code: "must_upgrade" } });
    expect(outbox.builds.newer.value).toBe("too_old");
    stop();
  });

  test("a batch held back by another tab makes this one too old, though this tab wrote before hearing", () => {
    const waiting = entry();
    const refused = entry({ state: "refused", reason: { code: "key_reused" } });
    store(waiting, refused);
    const outbox = new Outbox(answering(() => saved()).api);
    const stop = outbox.listen();
    const oldValue = localStorage.getItem(STORAGE_KEY);
    store({ ...waiting, reason: { code: "must_upgrade" } }, refused);
    const newValue = localStorage.getItem(STORAGE_KEY);
    // This tab writes, reading the hold, before the storage event comes.
    outbox.dismiss(refused);
    window.dispatchEvent(
      new StorageEvent("storage", { key: STORAGE_KEY, oldValue, newValue }),
    );
    expect(outbox.builds.newer.value).toBe("too_old");
    stop();
  });

  test("a batch held back before this tab loaded does not make it too old", async () => {
    // A tab held back while this build was too old...
    const held = new Outbox(answering(() => json(TOO_OLD, 409)).api);
    held.queue(draft(), named("held"));
    await vi.waitFor(() => expect(held.builds.newer.value).toBe("too_old"));
    const [stale] = stored() as Entry[];
    expect(stale?.reason).toEqual({ code: "must_upgrade" });
    // ...then the build is current again (rolled back), and this tab is
    // loaded since.
    const { api, sent } = answering(() => saved());
    const outbox = new Outbox(api);
    const stop = outbox.listen();
    // Another tab writes, the stale one still kept.
    storeElsewhere(stale, entry());
    expect(outbox.builds.newer.value).toBeNull();
    await outbox.send();
    expect(sent).toHaveLength(2);
    expect(states(outbox)).toEqual(["saved", "saved"]);
    stop();
  });

  test("an older build's tab's batch does not show here", () => {
    const outbox = new Outbox(answering(() => saved()).api);
    const stop = outbox.listen();
    storeOlder("inventory.outbox.v3", entry());
    window.dispatchEvent(
      new StorageEvent("storage", { key: "inventory.outbox.v3" }),
    );
    expect(outbox.entries.value).toEqual([]);
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
