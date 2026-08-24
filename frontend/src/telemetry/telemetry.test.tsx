/**
 * The browser half of one request being followable from a click.
 *
 * Two properties matter more than the rest and are asserted first: that a
 * device nobody asked about starts nothing at all -- no SDK, no exporter, no
 * header -- and that a token never stays in the address bar, because a link
 * carrying one is forwarded, screenshotted and pasted back.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { report, watch } from "./errors";
import { asked, claimed, forget, HEADER, LIFETIME, remember, settle, withoutIt } from "./flag";
import { Recording } from "./Recording";
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
    render(<Recording recording={false} />);

    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  });

  it("says so in words, and stopping it forgets the token", () => {
    remember(TOKEN);
    render(<Recording recording />);

    expect(screen.getByText(/Recording this device/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));

    expect(asked()).toBeNull();
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  });
});
