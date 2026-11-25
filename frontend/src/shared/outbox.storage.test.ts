import { describe, expect, test, vi } from "vitest";
import { answering, json, saved } from "../testing/fetch";
import {
  bodies,
  draft,
  entry,
  named,
  reasonSaid,
  states,
  store,
  stored,
} from "../testing/outbox";
import { keep, storeOutbox } from "../testing/saves";
import { Builds } from "./build";
import { say } from "./i18n";
import { Entry, Kept, Outbox, SAVED_MOST, STORAGE_KEY } from "./outbox";
import { PROBLEMS } from "./outbox-notes";

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
      {
        item_id: 5,
        quantity: "305",
        unit: "each",
        package_id: 9,
        package_size: "305",
      },
      { item_id: 6, quantity: "3", unit: "each" },
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
    const lines = [
      { item_id: 5, quantity: "305", unit: "each", package_id: 9 },
    ];
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
    expect(reasonSaid(outbox)).toBe(
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
    expect(reasonSaid(outbox)).toBe(
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
    expect(reasonSaid(outbox)).toBe(
      `Not recorded. Line 1: ${say(PROBLEMS.package_resized)}`,
    );
  });

  test("an older build's keys are neither read, sent, nor written", async () => {
    const older = entry();
    keep("inventory.outbox.v3", [older]);
    keep("inventory.outbox.v1", [entry({ state: "saved" })]);
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
    storeOutbox([theirs], "2999-01-01T00:00:00.000Z");
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
    // only such a field may be added here without a new key. Lost,
    // summary.boxes leaves a refusal naming a report by its number, as
    // before (inventory-tng-uwi3).
    const droppedAsBefore: string[] = ["summary.boxes"];
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
        "body.changed_from.unit",
        "body.changed_from.whole",
        "body.from_location_id",
        "body.install_number",
        "body.key",
        "body.kind",
        "body.kind = move | receipt | install | open | report",
        "body.lengths",
        "body.lengths.left",
        "body.lengths.open_package_key",
        "body.lengths.unit",
        "body.lines",
        "body.lines.item_id",
        "body.lines.open_package_key",
        "body.lines.package_id",
        "body.lines.package_size",
        "body.lines.quantity",
        "body.lines.unit",
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
        "reason.added",
        "reason.added = true",
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
        "reason.recorded.batch.changed_from.unit",
        "reason.recorded.batch.changed_from.whole",
        "reason.recorded.batch.from_location_id",
        "reason.recorded.batch.install_number",
        "reason.recorded.batch.key",
        "reason.recorded.batch.kind",
        "reason.recorded.batch.kind = move | receipt | install | open | report",
        "reason.recorded.batch.lengths",
        "reason.recorded.batch.lengths.left",
        "reason.recorded.batch.lengths.open_package_key",
        "reason.recorded.batch.lengths.unit",
        "reason.recorded.batch.lines",
        "reason.recorded.batch.lines.item_id",
        "reason.recorded.batch.lines.open_package_key",
        "reason.recorded.batch.lines.package_id",
        "reason.recorded.batch.lines.package_size",
        "reason.recorded.batch.lines.quantity",
        "reason.recorded.batch.lines.unit",
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
