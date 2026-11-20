import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import openapi from "../api/openapi.json";
import { messages as pseudo } from "../locales/pseudo.po";
import { answering, json, saved } from "../testing/fetch";
import {
  bodies,
  draft,
  entry,
  named,
  replies,
  said,
  states,
  store,
} from "../testing/outbox";
import { say, speak } from "./i18n";
import { Outbox, REPORT_AFTER, RETRY_MS, STORAGE_KEY } from "./outbox";
import {
  KEY_REUSED,
  NOT_ACCEPTED,
  NOT_RECORDED,
  ON_LINE,
  PROBLEMS,
  type ProblemKind,
  SOME_NOT_RECORDED,
  UNAVAILABLE,
} from "./outbox-notes";
import { telemetry } from "./telemetry";

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
