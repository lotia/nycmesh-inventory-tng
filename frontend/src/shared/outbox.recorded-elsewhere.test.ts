import { v4 as uuid } from "uuid";
import { describe, expect, test, vi } from "vitest";
import { answering, json, keyReused, saved, TOO_OLD } from "../testing/fetch";
import {
  bodies,
  draft,
  entry,
  named,
  reasonSaid,
  replies,
  states,
  store,
} from "../testing/outbox";
import { Builds } from "./build";
import { say } from "./i18n";
import { type Entry, Outbox } from "./outbox";
import { KEY_REUSED, noteOf, SOME_NOT_RECORDED } from "./outbox-notes";

describe("what another tab recorded (inventory-tng-0gg)", () => {
  /** The server's 409 for a key whose transaction 41, from location 1 to
   * 2, held `lines`. */
  const recordedReply = (
    lines = [{ item_id: 5, quantity: "3.000", unit: "each" }],
  ) =>
    keyReused(lines, {
      transaction_id: 41,
      from_location_id: 1,
      to_location_id: 2,
    });

  const changed = () =>
    draft({ lines: [{ item_id: 5, quantity: "4", unit: "each" }] });

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
              unit: "each",
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
    expect(reasonSaid(outbox)).toBe(say(SOME_NOT_RECORDED));
  });

  test("a keyless batch refused for its key still asks for a report", async () => {
    const { api } = answering(() => recordedReply());
    const outbox = new Outbox(api);
    outbox.queue(changed(), named("b"));
    await vi.waitFor(() => expect(states(outbox)).toEqual(["refused"]));
    expect(reasonSaid(outbox)).toBe(say(KEY_REUSED));
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
      draft({ changed_from: [{ item_id: 5, quantity: "9", unit: "each" }] }),
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
    expect(outbox.addedToCart(outbox.entries.value[1] as Entry)).toBe(true);
    expect(outbox.entries.value[1]?.reason).toMatchObject({ added: true });
    expect(outbox.entries.value[0]?.reason).toBeNull();
  });

  test("its other lines put in the cart (Add to cart) is kept so, of that one alone (inventory-tng-clvh)", () => {
    const problems = [{ names: { of: "line" as const, n: 1 }, kind: "x" }];
    const refused = (what: string): Entry =>
      entry({
        summary: named(what),
        state: "refused",
        reason: { code: "refused", problems },
      });
    const first = refused("a");
    const second = refused("b");
    store(first, second);
    const outbox = new Outbox(answering(() => saved()).api);
    expect(outbox.addedToCart(second)).toBe(true);
    expect(outbox.entries.value.map((e) => e.reason)).toEqual([
      { code: "refused", problems },
      { code: "refused", problems, added: true },
    ]);
    expect(new Outbox(answering(() => saved()).api).entries.value).toEqual(
      outbox.entries.value,
    );
  });

  test("one listed recorded elsewhere waits to be asked about while the batch under its key waits", async () => {
    const { api, sent } = answering(() => json({}, 500));
    const outbox = new Outbox(api);
    const key = uuid();
    outbox.queue(draft(), named("a"), key);
    await vi.waitFor(() => expect(sent).toHaveLength(1));
    expect(outbox.queue(changed(), named("b"), key)).toBe("refused");
    await outbox.send();
    const posted = await bodies(sent);
    expect(posted.map((body) => body.lines[0].quantity)).toEqual(
      posted.map(() => "3"),
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
