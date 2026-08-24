/**
 * The browser half of one request being followable from a click.
 *
 * Two properties matter more than the rest and are asserted first: that a
 * device nobody asked about starts nothing at all -- no SDK, no exporter, no
 * header -- and that a token never stays in the address bar, because a link
 * carrying one is forwarded, screenshotted and pasted back.
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { report, watch } from "./errors";
import {
  asked,
  claimed,
  forget,
  HEADER,
  LIFETIME,
  remember,
  runsOutIn,
  settle,
  withoutIt,
} from "./flag";
import { Recording } from "./Recording";
import { described, FAILURES, failed } from "./report";
import { recording, start, stop } from "./start";
import { ENDPOINT, wiring } from "./wiring";

const TOKEN = "f5d7fbfb1fff42b0:1wyD8F:OAFH-2HLmGoQgUwOM-Cucr6owx0UmgaeurdbbRWF4-g";

beforeEach(async () => {
  window.localStorage.clear();
  // The REAL teardown, awaited. Resetting a boolean left the provider, the
  // zone context manager and both instrumentations registered globally, so a
  // test after one that started the SDK ran against a live one: `report` made
  // recording spans a batch processor queued for POST out of jsdom, and the
  // assertion that nothing was running was true of the boolean and of nothing
  // else. It would have kept passing with the guard it protects removed.
  await stop();
});

afterEach(() => {
  vi.useRealTimers();
  // HERE RATHER THAN AT THE END OF EACH TEST BODY, which is where it was: a
  // failing assertion aborts before the last statement, so one genuine failure
  // left `fetch` globally replaced and cascaded into unrelated ones that hid
  // it. The asymmetry was visible on its face -- this block already restored
  // timers and not globals.
  vi.unstubAllGlobals();
});

describe("the flag", () => {
  it("is off until an administrator's link turns it on", () => {
    expect(asked()).toBeNull();
  });

  it("is read out of the address bar", () => {
    expect(claimed({ search: `?${"trace"}=${TOKEN}` } as Location)).toBe(TOKEN);
    expect(claimed({ search: "" } as Location)).toBeNull();
    expect(claimed({ search: "?trace=%20" } as Location)).toBeNull();
  });

  it("and the address it leaves behind keeps everything else", () => {
    const location = {
      pathname: "/S/7QK3M2XV9A",
      search: "?trace=abc&next=2",
      hash: "#top",
    } as Location;

    expect(withoutIt(location)).toBe("/S/7QK3M2XV9A?next=2#top");
  });

  it("runs out on its own", () => {
    const now = Date.parse("2026-08-24T10:00:00Z");
    remember(TOKEN, now);

    expect(asked(now + LIFETIME - 1)).toBe(TOKEN);
    expect(asked(now + LIFETIME)).toBeNull();
    expect(asked(now + 1)).toBeNull();
  });

  it("and can be stopped by hand", () => {
    remember(TOKEN);
    forget();

    expect(asked()).toBeNull();
  });

  it("is not taken out of the address bar until it is somewhere else", () => {
    // A private window, or site data blocked: `setItem` throws, `write`
    // reports false, and stripping the parameter anyway would leave the token
    // nowhere at all. Nothing to reload, and an administrator minting another
    // with nothing at either end saying why.
    const refusing = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("this browser stores nothing");
    });
    const replaced = vi.spyOn(window.history, "replaceState");
    window.history.replaceState(null, "", `/S/7QK3M2XV9A?trace=${TOKEN}`);
    replaced.mockClear();

    expect(settle()).toBe(TOKEN);
    expect(replaced).not.toHaveBeenCalled();
    expect(window.location.search).toContain(TOKEN);

    refusing.mockRestore();
    replaced.mockRestore();
    window.history.replaceState(null, "", "/");
  });

  it("and is taken out of it the moment it is", () => {
    window.history.replaceState(null, "", `/S/7QK3M2XV9A?trace=${TOKEN}`);

    expect(settle()).toBe(TOKEN);
    expect(window.location.search).toBe("");
    expect(asked()).toBe(TOKEN);

    window.history.replaceState(null, "", "/");
  });
});

describe("starting", () => {
  it("does nothing at all for a device nobody asked about", async () => {
    expect(await start(null)).toBe(false);
    expect(recording()).toBe(false);
  });

  it("starts once for a device that was", async () => {
    expect(await start(TOKEN)).toBe(true);
    expect(recording()).toBe(true);
    expect(await start(TOKEN)).toBe(false);
  });

  it("and stops for real, rather than only saying it has", async () => {
    await start(TOKEN);
    await stop();

    expect(recording()).toBe(false);
    // Started again afterwards, which a page whose provider was never shut
    // down could not do without registering a second set of instrumentations
    // onto the same fetch.
    expect(await start(TOKEN)).toBe(true);
  });

  it("pulls the SDK in only when it is going to be used", async () => {
    // The module `main.tsx` reaches at load must not reach the SDK, because
    // `@opentelemetry/context-zone` patches Promise and the timers for the
    // life of the page as an import side effect. Asserted against the source
    // rather than against a bundle: a static import is the thing that would
    // put it back, and it is visible here.
    const source = await import("./start?raw").then((held) => String(held.default));

    expect(source).not.toMatch(/^import .*@opentelemetry/m);
    expect(source).toMatch(/await import\("\.\/sdk"\)/);
  });

  it("posts to this origin, because the policy is not widened", () => {
    expect(ENDPOINT.startsWith("/")).toBe(true);
  });

  it("and carries the token the ingest path requires", () => {
    expect(wiring(TOKEN)).toEqual({ url: ENDPOINT, headers: { [HEADER]: TOKEN } });
  });
});

describe("failures nobody caught", () => {
  it("are recorded without throwing when nothing is running", () => {
    expect(() => report(new Error("boom"), "window.onerror")).not.toThrow();
    expect(() => report("a string nobody wrapped", "unhandledrejection")).not.toThrow();
  });

  it("are listened for, and can be stopped listening for", () => {
    const target = { addEventListener: vi.fn(), removeEventListener: vi.fn() } as unknown as Window;

    watch(target)();

    expect(target.addEventListener).toHaveBeenCalledTimes(2);
    expect(target.removeEventListener).toHaveBeenCalledTimes(2);
  });
});

describe("the indicator", () => {
  it("is absent unless this device is being recorded", () => {
    render(<Recording />);

    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  });

  it("says so in words, and stopping it forgets the token", async () => {
    remember(TOKEN);
    await start(TOKEN);
    render(<Recording />);

    expect(screen.getByText(/Recording this device/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));

    expect(asked()).toBeNull();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Stop" })).toBeNull());
  });

  it("and goes when the SDK does, rather than when it was told to", async () => {
    // Why the badge asks rather than remembering is written where it asks --
    // `Recording.tsx`. This is the case that would have caught it: nothing but
    // the button used to be able to take the badge away.
    remember(TOKEN);
    await start(TOKEN);
    render(<Recording />);

    expect(screen.getByText(/Recording this device/)).toBeInTheDocument();

    await act(async () => {
      await stop();
    });

    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  });
});

describe("running out on its own", () => {
  it("tears the SDK down at the hour, which is what flag.ts promised", async () => {
    vi.useFakeTimers();
    const now = Date.parse("2026-08-24T10:00:00Z");
    vi.setSystemTime(now);
    remember(TOKEN, now);

    await start(TOKEN);
    expect(recording()).toBe(true);

    // A minute short: nothing has happened yet, which is the half that would
    // pass against a teardown armed for the wrong moment.
    await vi.advanceTimersByTimeAsync(LIFETIME - 60_000);
    expect(recording()).toBe(true);

    await vi.advanceTimersByTimeAsync(60_000);

    expect(recording()).toBe(false);
  });

  it("and the badge goes with it", async () => {
    vi.useFakeTimers();
    const now = Date.parse("2026-08-24T10:00:00Z");
    vi.setSystemTime(now);
    remember(TOKEN, now);
    await start(TOKEN);
    render(<Recording />);

    expect(screen.getByText(/Recording this device/)).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIFETIME);
    });

    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  });

  it("falls back to the declared hour when this device could store nothing", async () => {
    // A private window: `settle` deliberately hands back a token it could not
    // store, so `runsOutIn` has nothing to answer from. Without the fallback
    // nothing is armed at all and the SDK runs for the life of the page --
    // on a volunteer's data -- while the badge promises otherwise.
    vi.useFakeTimers();
    const refusing = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("this browser stores nothing");
    });

    expect(await start(TOKEN)).toBe(true);
    expect(runsOutIn()).toBeNull();

    await vi.advanceTimersByTimeAsync(LIFETIME);

    expect(recording()).toBe(false);
    refusing.mockRestore();
  });

  it("arms nothing for a device that is not recording", async () => {
    // `start(null)` returns before the timer, so a page nobody asked about
    // schedules no work at all -- the same promise the SDK import keeps.
    vi.useFakeTimers();

    expect(await start(null)).toBe(false);

    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("stopping while the SDK is still loading", () => {
  it("tears down what the attempt built rather than leaving it registered", async () => {
    // `start.ts` says what the window between claiming `started` and the chunk
    // resolving is, and what assigning the result afterwards would leave
    // behind. This is that case, driven.
    remember(TOKEN);
    const starting = start(TOKEN);
    await stop();

    expect(await starting).toBe(false);
    expect(recording()).toBe(false);
    // And the page can still be started again, which it could not if the
    // instrumentations from the abandoned attempt were still patched on.
    expect(await start(TOKEN)).toBe(true);
  });

  it("and a second stop waits for the first rather than returning early", async () => {
    remember(TOKEN);
    await start(TOKEN);

    await Promise.all([stop(), stop()]);

    expect(recording()).toBe(false);
    expect(await start(TOKEN)).toBe(true);
  });
});

describe("what is reported whatever the flag says", () => {
  it("goes to the backend, not to the collector", () => {
    const posting = vi.fn(async () => new Response("", { status: 204 }));
    vi.stubGlobal("fetch", posting);

    failed(new Error("the decode loop stopped"), "scan", "decode-loop");

    const [path, init] = posting.mock.calls[0] as unknown as [string, RequestInit];

    expect(path).toBe(FAILURES);
    expect(JSON.parse(String(init.body))).toEqual({
      kind: "decode-loop",
      where: "scan",
      detail: expect.stringContaining("the decode loop stopped"),
    });
  });

  it("and carries the two headers every other write carries", () => {
    const posting = vi.fn(async () => new Response("", { status: 204 }));
    vi.stubGlobal("fetch", posting);
    // biome-ignore lint/suspicious/noDocumentCookie: arranged the way client.test.ts arranges one.
    document.cookie = "csrftoken=a-token-django-set";
    remember(TOKEN);

    failed(new Error("boom"), "app");

    const [, init] = posting.mock.calls[0] as unknown as [string, RequestInit];
    const headers = init.headers as Record<string, string>;

    // What each of the two is for, and what leaving it off cost, is written
    // where they are set -- `report.ts`.
    expect(headers["X-CSRFToken"]).toBe("a-token-django-set");
    expect(headers[HEADER]).toBe(TOKEN);
  });

  it("and is bounded, because a message is whatever somebody threw", () => {
    expect(described(new Error("x".repeat(9000)))).toHaveLength(2000);
    expect(described("not an Error")).toBe("not an Error");
  });

  it("never becomes the failure itself", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => {
        throw new Error("this browser refused outright");
      }),
    );

    expect(() => failed(new Error("boom"), "app")).not.toThrow();
  });
});
