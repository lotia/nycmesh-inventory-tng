import { describe, expect, test, vi } from "vitest";
import { answering, json, saved, TOO_OLD } from "../testing/fetch";
import {
  draft,
  entry,
  named,
  said,
  states,
  store,
  stored,
} from "../testing/outbox";
import { Builds, HEADER, MUST_UPGRADE } from "./build";
import { say } from "./i18n";
import { type Entry, Outbox } from "./outbox";
import { telemetry } from "./telemetry";

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
    // Listed with what older builds left (scan/older-builds.ts), not here,
    // and never sent again.
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
