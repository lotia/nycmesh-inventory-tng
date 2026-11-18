import { fireEvent, render, screen, within } from "@testing-library/preact";
import { describe, expect, test, vi } from "vitest";
import { FROM, NO_NAME_PICKED, PICK_A_PLACE, YOUR_NAME } from "./CartForm";
import { say } from "./i18n";
import { Outbox } from "./outbox";
import { COULD_NOT_LOAD, ScanPage, TRY_AGAIN } from "./ScanPage";
import { CATALOGUE } from "./test-catalogue";
import { answering, deferred, json, saved } from "./test-fetch";
import { expectBold, isCatalogue, open, ready } from "./test-scan-page";

// The camera, faked: it sees whatever a test puts in `inView`.
vi.mock("./camera", () => ({ openCamera: vi.fn() }));

describe("loading", () => {
  test("the catalogue fills the choices", async () => {
    open();
    await ready();
    const names = within(screen.getByLabelText(say(YOUR_NAME))).getAllByRole(
      "option",
    );
    expect(names.map((option) => option.textContent)).toEqual([
      say(NO_NAME_PICKED),
      "Ada",
      "Grace",
    ]);
    const places = within(screen.getByLabelText(say(FROM))).getAllByRole(
      "option",
    );
    expect(places.map((option) => option.textContent)).toEqual([
      say(PICK_A_PLACE),
      "Hub",
      "Hub › Shelf A",
      "Van",
    ]);
  });

  test("an older load answering after a newer one is not shown", async () => {
    const older = deferred();
    const first = answering(() => older.promise).api;
    const outbox = new Outbox(first);
    const { rerender } = render(
      <ScanPage api={first} outbox={outbox} reload={vi.fn()} />,
    );
    const newer = {
      ...CATALOGUE,
      volunteers: [{ id: 3, name: "Hedy" }],
    };
    const second = answering((request) =>
      isCatalogue(request) ? json(newer, 200) : saved(),
    ).api;
    rerender(<ScanPage api={second} outbox={outbox} reload={vi.fn()} />);
    await screen.findByRole("option", { name: "Hedy" });
    older.resolve(json(CATALOGUE, 200));
    await new Promise((done) => setTimeout(done, 0));
    expect(screen.queryByRole("option", { name: "Ada" })).toBeNull();
  });

  test("an unregistered browser is sent to register", async () => {
    const { api } = answering(() => json({ reason: "unregistered" }, 403));
    open(api);
    const alert = await screen.findByRole("alert");
    // A failure: in bold, the link too (Ali, inventory-tng-93z).
    expectBold(
      alert,
      "This browser isn't registered yet. Register it, then come back.",
    );
    expect(within(alert).getByRole("link").getAttribute("href")).toBe(
      "/register",
    );
  });

  test("a withdrawn registration says so", async () => {
    const { api } = answering(() => json({ reason: "revoked" }, 403));
    open(api);
    expect((await screen.findByRole("alert")).textContent).toContain(
      "registration was withdrawn",
    );
  });

  test("a failed load can be tried again", async () => {
    let first = true;
    const { api } = answering(() => {
      if (first) {
        first = false;
        return json({ reason: "database_unavailable" }, 503);
      }
      return json(CATALOGUE, 200);
    });
    open(api);
    expect((await screen.findByText(say(COULD_NOT_LOAD))).tagName).toBe(
      "STRONG",
    );
    fireEvent.click(screen.getByRole("button", { name: say(TRY_AGAIN) }));
    await ready();
  });

  test("a page left open fetches a stale catalogue again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let catalogue = CATALOGUE;
    const { api } = answering(() => json(catalogue, 200));
    open(api);
    await ready();
    catalogue = {
      ...CATALOGUE,
      volunteers: [...CATALOGUE.volunteers, { id: 3, name: "Hedy" }],
    };
    await vi.advanceTimersByTimeAsync(11 * 60_000);
    expect(await screen.findByRole("option", { name: "Hedy" })).toBeDefined();
  });
});
