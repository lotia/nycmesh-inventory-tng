import { v4 as uuid } from "uuid";
import { describe, expect, test, vi } from "vitest";
import { answering, deferred, json, saved } from "../testing/fetch";
import {
  bodies,
  draft,
  entry,
  named,
  replies,
  states,
  store,
  stored,
} from "../testing/outbox";
import { refusing } from "../testing/storage";
import { batchId, type Entry, Outbox } from "./outbox";

/** Storage that reads but refuses every write. */
const full = refusing("setItem");

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
    expect(outbox.addedToCart(third)).toBe(true);
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
