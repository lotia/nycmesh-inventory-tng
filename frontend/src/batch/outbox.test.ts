/**
 * The queue, and the one thing it exists to be sure of: a batch saved with no
 * signal reaches the ledger once when the signal comes back.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  discardBatch,
  forgetOutbox,
  outbox,
  queueBatch,
  STORAGE_KEY,
  sendOutbox,
  waiting,
} from "./outbox";
import {
  answered,
  batch,
  fetching,
  forgetWhatWasStubbed,
  nothing,
  nothingQueued,
  recorded,
  stubFetch,
} from "./testFixtures";

/** The bodies that were posted, in order. */
function posted(): unknown[] {
  return fetching.mock.calls.map(([, init]) => JSON.parse(String((init as RequestInit).body)));
}

/** The keys they went under, which is what the server recognises a replay by. */
function keysPosted(): string[] {
  return posted().map((body) => (body as { idempotency_key: string }).idempotency_key);
}

/** A queue left on the device by a session that has gone. */
function onTheDevice(entries: unknown[]): void {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
}

beforeEach(() => {
  nothingQueued();
  stubFetch();
});

afterEach(forgetWhatWasStubbed);

describe("holding a batch", () => {
  it("keeps it where a browser restart cannot lose it", () => {
    queueBatch(batch(), 1_700_000_000_000);
    const written = window.localStorage.getItem(STORAGE_KEY);

    // A restart is the module remembering nothing while the device remembers
    // everything -- so what the reset wipes is put back, a phone rebooting
    // not being in the habit of clearing its own storage.
    forgetOutbox();
    window.localStorage.setItem(STORAGE_KEY, String(written));

    expect(outbox()).toEqual([{ ...batch(), queuedAt: 1_700_000_000_000, outcome: null }]);
  });

  it("says whether the device took it, rather than reporting a write it never saw", () => {
    expect(queueBatch(batch())).toBe(true);

    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });

    // Nothing was stored, so the caller is still the only one holding this
    // batch. Answering true here is how a cart is emptied into nowhere.
    expect(queueBatch(batch("key-2", "Cat6"))).toBe(false);
    // And it is not left here either, where it would be drawn as held and
    // then be gone at the next reload.
    expect(outbox().map((entry) => entry.key)).toEqual(["key-1"]);
  });

  it("holds one batch per key, however many times Save was pressed", () => {
    queueBatch(batch());
    queueBatch(batch());

    expect(outbox()).toHaveLength(1);
  });

  it("ignores a stored entry it cannot read, rather than posting nonsense", () => {
    // A body that came back as a string would be sent as one. An entry with
    // no key is a batch nothing can recognise as a replay.
    onTheDevice([{ body: "movements", what: 1 }]);

    expect(outbox()).toEqual([]);
  });

  it("ignores one whose body is a list, which is not a request this API reads", () => {
    onTheDevice([{ ...batch(), body: [], queuedAt: 1, outcome: null }]);

    expect(outbox()).toEqual([]);
  });

  it("keeps the entries it can read when one of them is nonsense", () => {
    // All or nothing here is the worse failure by far: one corrupted row is
    // one batch, and dropping the queue for it loses every other batch on the
    // device -- then the next write puts the empty array over them.
    onTheDevice([{ body: "movements" }, { ...batch("key-2", "Cat6"), queuedAt: 1, outcome: null }]);

    expect(outbox().map((entry) => entry.key)).toEqual(["key-2"]);
  });

  it("forgets one when it is discarded", () => {
    queueBatch(batch("key-1"));
    queueBatch(batch("key-2", "Cat6"));

    discardBatch("key-1");

    expect(outbox().map((held) => held.key)).toEqual(["key-2"]);
  });
});

describe("sending what is held", () => {
  it("posts the body it was given, key and all", async () => {
    queueBatch(batch());
    fetching.mockResolvedValueOnce(recorded());

    await sendOutbox();

    expect(posted()).toEqual([batch().body]);
  });

  it("marks it recorded, and does not send it a second time", async () => {
    queueBatch(batch());
    fetching.mockResolvedValueOnce(recorded());
    await sendOutbox();

    await sendOutbox();

    expect(fetching).toHaveBeenCalledTimes(1);
    expect(outbox()[0].outcome).toEqual({ recorded: true, detail: "Recorded." });
    expect(waiting(outbox())).toEqual([]);
  });

  it("passes on what the ledger warned about", async () => {
    queueBatch(batch());
    fetching.mockResolvedValueOnce(recorded([{ detail: "131 Broome now shows -4 LiteBeam." }]));

    await sendOutbox();

    expect(outbox()[0].outcome?.detail).toContain("131 Broome now shows -4 LiteBeam.");
  });

  it("treats a batch the server had already recorded as recorded", async () => {
    // The 200 the idempotency key buys. This is the case the whole queue is
    // for: the batch went in and the answer never arrived.
    queueBatch(batch());
    fetching.mockResolvedValueOnce(new Response(JSON.stringify({ id: 12, warnings: [] }), {}));

    await sendOutbox();

    expect(outbox()[0].outcome?.recorded).toBe(true);
  });

  it("leaves it waiting when there is still nothing at the other end", async () => {
    queueBatch(batch());
    fetching.mockImplementationOnce(nothing);

    await sendOutbox();

    expect(waiting(outbox())).toHaveLength(1);
  });

  it("keeps the same key on every attempt, so the ledger records it once", async () => {
    queueBatch(batch());
    fetching.mockImplementationOnce(nothing);
    await sendOutbox();
    fetching.mockResolvedValueOnce(recorded());

    await sendOutbox();

    expect(keysPosted()).toEqual(["key-1", "key-1"]);
  });

  it("sends the oldest first", async () => {
    queueBatch(batch("key-1"));
    queueBatch(batch("key-2", "Cat6"));
    fetching.mockImplementation(() => Promise.resolve(recorded()));

    await sendOutbox();

    expect(keysPosted()).toEqual(["key-1", "key-2"]);
    expect(outbox().map((entry) => entry.outcome?.recorded)).toEqual([true, true]);
  });

  it("settles a success it cannot read, instead of taking it for a lost signal", async () => {
    // The null `apiPost` hands back for a body it could not parse; see
    // isRecordedBatch. Read where a throw is taken for a lost connection,
    // this settles nothing and nothing behind it moves either.
    queueBatch(batch());
    fetching.mockResolvedValueOnce(new Response("", { status: 200 }));

    await sendOutbox();

    expect(outbox()[0].outcome).toEqual({ recorded: true, detail: "Recorded." });
  });

  it("does not post one the volunteer discarded while the run was in flight", async () => {
    // The queue is read again before each attempt for this: an append-only
    // ledger cannot be told afterwards that the batch was withdrawn.
    queueBatch(batch("key-1"));
    queueBatch(batch("key-2", "Cat6"));
    let answer: () => void = () => undefined;
    fetching.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          answer = () => resolve(recorded());
        }),
    );
    fetching.mockImplementation(() => Promise.resolve(recorded()));

    const run = sendOutbox();
    discardBatch("key-2");
    answer();
    await run;

    expect(keysPosted()).toEqual(["key-1"]);
  });

  it("attempts one key once in a pass, and lets a later one wait for the next", async () => {
    // What the `attempted` set buys, since it is not what stops a discarded
    // batch being posted -- a discarded entry is simply not in the queue any
    // more. It is what ends the pass: one attempt per key, without the loop
    // having to trust that settling an entry took it out of `waiting`.
    queueBatch(batch("key-1"));
    queueBatch(batch("key-2", "Cat6"));
    let answer: () => void = () => undefined;
    let reached: () => void = () => undefined;
    const secondAttempt = new Promise<void>((resolve) => {
      reached = resolve;
    });
    fetching.mockResolvedValueOnce(recorded());
    fetching.mockImplementationOnce(() => {
      reached();
      return new Promise<Response>((resolve) => {
        answer = () => resolve(recorded());
      });
    });

    const run = sendOutbox();
    // key-1 has been sent and settled; the pass is now on key-2. Queueing
    // under key-1 again puts a waiting entry behind the point it has reached.
    await secondAttempt;
    queueBatch(batch("key-1"));
    answer();
    await run;

    expect(keysPosted()).toEqual(["key-1", "key-2"]);
    expect(waiting(outbox())).toHaveLength(1);

    // And the next trigger takes it, which is why waiting costs nothing.
    fetching.mockResolvedValueOnce(recorded());
    await sendOutbox();

    expect(keysPosted()).toEqual(["key-1", "key-2", "key-1"]);
  });

  it("gives a trigger that arrived mid-run a pass of its own", async () => {
    // The trigger that matters is a save getting through, which is the best
    // evidence there is that the network is back -- and it lands while a run
    // started on the old signal is still failing.
    queueBatch(batch());
    let giveUp: () => void = () => undefined;
    fetching.mockImplementationOnce(
      () =>
        new Promise<Response>((_, reject) => {
          giveUp = () => reject(new TypeError("Failed to fetch"));
        }),
    );
    fetching.mockImplementationOnce(() => Promise.resolve(recorded()));

    const first = sendOutbox();
    const second = sendOutbox();
    giveUp();
    await Promise.all([first, second]);

    expect(fetching).toHaveBeenCalledTimes(2);
    expect(waiting(outbox())).toEqual([]);
  });

  it("does not start a second run over the same batch while one is in flight", async () => {
    queueBatch(batch());
    let answer: () => void = () => undefined;
    fetching.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          answer = () => resolve(recorded());
        }),
    );

    const first = sendOutbox();
    const second = sendOutbox();
    answer();
    await Promise.all([first, second]);

    expect(fetching).toHaveBeenCalledTimes(1);
  });
});

describe("a batch the server will never take", () => {
  it("stops trying, and says why", async () => {
    queueBatch(batch());
    fetching.mockResolvedValueOnce(answered(400, "Nothing was saved."));
    await sendOutbox();

    await sendOutbox();

    expect(fetching).toHaveBeenCalledTimes(1);
    expect(outbox()[0].outcome).toEqual({ recorded: false, detail: "Nothing was saved." });
  });

  it("does not hold up the batches behind it", async () => {
    queueBatch(batch("key-1"));
    queueBatch(batch("key-2", "Cat6"));
    fetching.mockResolvedValueOnce(answered(409, "That is not a check-out."));
    fetching.mockResolvedValueOnce(recorded());

    await sendOutbox();

    expect(outbox().map((held) => held.outcome?.recorded)).toEqual([false, true]);
  });

  it("keeps a batch a lapsed session refused, because that is about the session", async () => {
    // A phone in a pocket loses its session, not its work. See NOT_NOW.
    queueBatch(batch());
    fetching.mockResolvedValueOnce(answered(401, "Not signed in."));
    await sendOutbox();
    fetching.mockResolvedValueOnce(answered(403, "Forbidden."));

    await sendOutbox();

    expect(fetching).toHaveBeenCalledTimes(2);
    expect(waiting(outbox())).toHaveLength(1);
  });

  it("keeps a throttled batch, which is a refusal about now rather than about it", async () => {
    queueBatch(batch());
    fetching.mockResolvedValueOnce(answered(429, "Too many."));

    await sendOutbox();

    expect(waiting(outbox())).toHaveLength(1);
  });

  it("keeps a batch the server broke on, and stops the run there", async () => {
    // Whatever is wrong with the server is not going to be different for the
    // next batch in the queue, and the volunteer's lines are not the problem.
    queueBatch(batch("key-1"));
    queueBatch(batch("key-2", "Cat6"));
    fetching.mockResolvedValueOnce(answered(500, "Sorry."));

    await sendOutbox();

    expect(fetching).toHaveBeenCalledTimes(1);
    expect(waiting(outbox())).toHaveLength(2);
  });
});
