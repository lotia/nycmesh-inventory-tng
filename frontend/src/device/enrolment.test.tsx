/**
 * The screen a device meets before this app will answer it, and the token it
 * keeps afterwards.
 *
 * PROVISIONAL, with what it tests: inventory-tng-81f7.4 deletes this file with
 * `device/credential.ts` and `device/Enrolment.tsx`.
 *
 * WHAT IS UNDER TEST is the friction, because that is what the room is being
 * asked to judge. Not the credential -- it is an opaque token and the module
 * says so -- but which screens appear, when, and what a second browser gets.
 * The one case that carries the whole of act five is the last: a device that
 * has not enrolled is asked again, however many times the app has already been
 * opened somewhere else.
 */
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Enrolment as EnrolmentState } from "../api/capabilities";
import { apiGet } from "../api/client";
import { callsTo, renderScreen, stubSession, VOLUNTEER } from "../testHarness";
import { forget, HEADER, held, remember } from "./credential";
import { EnrolmentGate } from "./Enrolment";

/** `/api/me` answering with one posture, and `/api/devices` answering `minted`. */
function deployment(enrolment: EnrolmentState, minted: () => Response): void {
  stubSession({ ...VOLUNTEER, enrolment }, (async (path: string) => {
    if (typeof path === "string" && path.startsWith("/api/devices")) {
      return minted();
    }
    return new Response(JSON.stringify({ count: 0, results: [] }), { status: 200 });
  }) as unknown as typeof fetch);
}

const minted = (token = "signed.token.here") =>
  new Response(JSON.stringify({ token }), { status: 201 });

/** What the app sent to `/api/devices`, as bodies. */
function enrolments(): unknown[] {
  return callsTo("/api/devices").map((call) =>
    JSON.parse(String(((call as unknown[])[1] as RequestInit).body)),
  );
}

const setUp = () => screen.getByRole("button", { name: /set this device up/i });

/** The gate with something recognisable behind it. Twelve tests render this. */
const gate = () =>
  renderScreen(
    <EnrolmentGate>
      <p>the app</p>
    </EnrolmentGate>,
  );

beforeEach(() => {
  window.localStorage.clear();
  // jsdom has no navigation, and the screen reloads once a device is enrolled
  // for the reason `Enrolment.tsx` gives. Replaced rather than spied on: it is
  // not configurable on jsdom's own Location.
  vi.stubGlobal("location", { ...window.location, reload: vi.fn() });
});

afterEach(() => {
  vi.unstubAllGlobals();
  // The private-window case below replaces `setItem` on the prototype, which
  // is global and would otherwise follow this file into the next one -- and
  // the failure it caused would be in a test that never mentions storage.
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("the gate", () => {
  it("draws nothing at all until the server has answered", async () => {
    // The fallback is `not_required`, so a gate that read it before /api/me
    // resolved mounted the whole app and drew a screenful of refusals before
    // swapping itself in -- in front of the room the demo was convened for.
    // Initialised rather than left null: TypeScript cannot see that the
    // Promise's executor has run, so a nullable one narrows to `never` at the
    // call below and fails `npm run typecheck`.
    let answer: () => void = () => {};
    const held = new Promise<void>((resolve) => {
      answer = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => {
        if (typeof path === "string" && path.startsWith("/api/me")) {
          await held;
          return new Response(JSON.stringify({ ...VOLUNTEER, enrolment: "self" }), { status: 200 });
        }
        return new Response(JSON.stringify({ count: 0, results: [] }), { status: 200 });
      }),
    );

    gate();

    expect(screen.queryByText("the app")).toBeNull();
    expect(screen.queryByRole("button", { name: /set this device up/i })).toBeNull();
    answer();
    expect(await screen.findByRole("button", { name: /set this device up/i })).toBeTruthy();
  });

  it("draws the app when the session could not be read at all", async () => {
    // A failed read will not arrive later, so a gate waiting for one would
    // wait for ever. Nobody is the right answer, exactly as it is everywhere
    // else this app reads that endpoint.
    // Everything refused, `/api/me` included -- which is what `stubSession`
    // cannot arrange, because its whole job is to answer that one.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("nope", { status: 500 })),
    );

    gate();

    expect(await screen.findByText("the app")).toBeTruthy();
  });

  it("draws the app where this deployment asks for no device", async () => {
    deployment("not_required", minted);

    gate();

    expect(await screen.findByText("the app")).toBeTruthy();
  });

  it("draws the app once this device has enrolled", async () => {
    deployment("enrolled", minted);

    gate();

    expect(await screen.findByText("the app")).toBeTruthy();
  });

  it("asks a device that has not enrolled, instead of the app", async () => {
    deployment("self", minted);

    gate();

    expect(await screen.findByRole("button", { name: /set this device up/i })).toBeTruthy();
    expect(screen.queryByText("the app")).toBeNull();
  });

  it("asks for nothing beyond a tap where any device may register itself", async () => {
    deployment("self", minted);

    gate();
    await screen.findByRole("button", { name: /set this device up/i });

    expect(screen.queryByLabelText(/enrolment code/i)).toBeNull();
  });

  it("asks for the code from the room where the posture wants one", async () => {
    deployment("code", minted);

    gate();

    expect(await screen.findByLabelText(/enrolment code/i)).toBeTruthy();
  });
});

describe("enrolling", () => {
  it("keeps the token and reloads into the app", async () => {
    deployment("self", () => minted("kept.token"));

    gate();
    fireEvent.click(await screen.findByRole("button", { name: /set this device up/i }));

    await waitFor(() => expect(held()).toBe("kept.token"));
    expect(window.location.reload).toHaveBeenCalled();
  });

  it("sends the code somebody typed, and nothing where none is asked for", async () => {
    deployment("code", minted);

    gate();
    fireEvent.change(await screen.findByLabelText(/enrolment code/i), {
      target: { value: " grand-street " },
    });
    fireEvent.click(setUp());

    await waitFor(() => expect(enrolments()).toEqual([{ code: "grand-street" }]));
  });

  it("will not send an empty code, so a tap cannot ask a question with no answer", async () => {
    deployment("code", minted);

    gate();
    await screen.findByLabelText(/enrolment code/i);

    expect(setUp().hasAttribute("disabled")).toBe(true);
  });

  it("says what the server said when the code is wrong", async () => {
    deployment(
      "code",
      () =>
        new Response(JSON.stringify({ detail: "That is not this deployment's enrolment code." }), {
          status: 403,
        }),
    );

    gate();
    fireEvent.change(await screen.findByLabelText(/enrolment code/i), {
      target: { value: "saratoga" },
    });
    fireEvent.click(setUp());

    expect(await screen.findByText(/not this deployment's enrolment code/i)).toBeTruthy();
    expect(held()).toBeNull();
  });

  it("says so rather than looping when the browser will not keep the token", async () => {
    // A private window: `setItem` throws, and the token is minted once and
    // never handed back, so reloading would return to this same screen for
    // ever with nothing saying why.
    deployment("self", minted);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });

    gate();
    fireEvent.click(await screen.findByRole("button", { name: /set this device up/i }));

    expect(await screen.findByText(/would not keep the registration/i)).toBeTruthy();
    expect(window.location.reload).not.toHaveBeenCalled();
  });
});

describe("the credential", () => {
  it("is nothing at all on a device that never enrolled", () => {
    expect(held()).toBeNull();
  });

  it("survives being stored and read back", () => {
    expect(remember("a.token")).toBe(true);
    expect(held()).toBe("a.token");
  });

  it("is nothing again once forgotten -- which is act five's second phone", () => {
    remember("a.token");
    forget();
    expect(held()).toBeNull();
  });

  it("ignores something of another shape left under its key", () => {
    window.localStorage.setItem("inventory.device", JSON.stringify({ nonsense: 1 }));
    expect(held()).toBeNull();
  });

  it("rides on every request once it is held", async () => {
    remember("a.token");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 200 })),
    );

    await apiGet("/api/volunteers");

    const [, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(new Headers((init as RequestInit).headers).get(HEADER)).toBe("a.token");
  });

  it("is absent from a request made by a device that never enrolled", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 200 })),
    );

    await apiGet("/api/volunteers");

    const [, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(new Headers((init as RequestInit).headers).has(HEADER)).toBe(false);
  });
});
