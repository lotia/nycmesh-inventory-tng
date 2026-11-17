/**
 * Every component, rendered in the pseudo-locale, which accents every
 * letter: a word still in plain English was not read through the
 * catalogue (docs/decisions/0022). The names in the catalogue and in
 * storage here are not English either, so any English is the page's own.
 * What a component says only in some states has a part of its own below
 * for those states.
 */
import { signal } from "@preact/signals";
import { fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, expect, test, vi } from "vitest";
import { DecoderUnavailable } from "./camera";
import { type Catalogue, freshCart, type Item, storeCart } from "./cart";
import { suggest } from "./combobox";
import { say, speak } from "./i18n";
import { messages as pseudo } from "./locales/pseudo.po";
import { OpenPackageCard } from "./OpenPackageCard";
import { Outbox } from "./outbox";
import { RegisterPage } from "./RegisterPage";
import { guardRetire } from "./retire";
import { Scanner } from "./Scanner";
import { HELD, ScanPage } from "./ScanPage";
import { answering, deferred, json } from "./test-fetch";
import {
  KEY,
  keep,
  moveBody,
  OTHER_KEY,
  olderSave,
  outboxEntry,
  storeOutbox,
} from "./test-saves";

afterEach(() => {
  vi.restoreAllMocks();
  // What a part wrote to the document itself, which cleanup leaves.
  document.body.replaceChildren();
});

/** What a value may say that is data, not the page's words: a unit's key
 * as stored, which the locale-aware display of units will say
 * (inventory-tng-ist.2). */
const DATA = new Set(["each"]);

const OPEN_KEY = "11111111-1111-4111-8111-111111111111";

const radio: Item = {
  id: 100,
  name: "Ραδιόφωνο",
  unit: "each",
  category: "Ραδιόφωνα",
  manufacturer: null,
  part_number: null,
  old_code: null,
  search_text: "Ραδιόφωνο",
  aliases: [],
  packages: [
    {
      id: 1000,
      name: "Κουτί",
      size: "4.000",
      track_opened: false,
      retired: false,
      short_below: null,
      sealed: [{ location_id: 10, count: 2 }],
    },
    {
      id: 1002,
      name: "Παλιό κουτί",
      size: "2.000",
      track_opened: false,
      retired: true,
      short_below: null,
      sealed: [{ location_id: 10, count: 1 }],
    },
  ],
  retired: false,
  retired_packages: [{ id: 1003, name: "Ζεύγος", size: "2.000" }],
};

const cable: Item = {
  ...radio,
  id: 101,
  name: "Καλώδιο",
  unit: "m",
  category: "Καλώδια",
  search_text: "Καλώδιο",
  packages: [
    {
      id: 1001,
      name: "Καρούλι",
      size: "305.000",
      track_opened: true,
      retired: false,
      short_below: "15",
      sealed: [{ location_id: 10, count: 3 }],
    },
  ],
  retired_packages: [],
};

const retired: Item = {
  ...radio,
  id: 104,
  name: "Παλιό ραδιόφωνο",
  search_text: "Παλιό ραδιόφωνο",
  packages: [],
  retired: true,
};

const OPEN = {
  id: 1,
  key: OPEN_KEY,
  item_id: 101,
  package_id: 1001,
  package_name: "Καρούλι",
  location_id: 10,
  opened_at: "2026-11-03T10:00:00Z",
  left: "4.000",
  short_below: "15",
};

const CATALOGUE: Catalogue = {
  settings: { name_check_minutes: 15 },
  volunteers: [
    { id: 1, name: "Άδα" },
    { id: 2, name: "Γκρέις" },
  ],
  locations: [
    { id: 10, name: "Κέντρο", kind: "store", parent_id: null },
    { id: 12, name: "Βαν", kind: "vehicle", parent_id: null },
  ],
  items: [radio, cable, retired],
  open_packages: [OPEN],
  retired_items: [
    {
      id: 103,
      name: "Νάνο",
      unit: "each",
      packages: [{ id: 1004, name: "Δέμα", size: "2.000" }],
    },
  ],
  saved: [],
  reading_rules: [],
  labels: [
    { code: "S4HELF", item_id: null, package_id: null, location_id: 10 },
    { code: "B3ZT9W", item_id: 100, package_id: null, location_id: null },
  ],
};

/** The scan page, its catalogue answered with `status`; batches never
 * answered, so each stays as stored. */
function scanPage(status = 200): Outbox {
  const never = deferred();
  const { api } = answering((request) =>
    new URL(request.url).pathname !== "/api/catalogue"
      ? never.promise
      : status === 200
        ? json(CATALOGUE, 200)
        : json({ reason: "revoked" }, status),
  );
  const outbox = new Outbox(api);
  render(<ScanPage api={api} outbox={outbox} reload={() => {}} />);
  return outbox;
}

/** The scan page once its catalogue is shown. */
async function loaded(): Promise<Outbox> {
  const outbox = scanPage();
  await screen.findByRole("searchbox");
  return outbox;
}

/** A batch as the outbox keeps it, under the `n`th key, of `fields`. */
function entry(n: number, fields: Record<string, unknown> = {}) {
  const entry = outboxEntry({
    body: moveBody({
      key: `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
      install_number: null,
      lines: [
        { item_id: 100, quantity: "8", package_id: 1000, package_size: "4" },
        { item_id: 101, quantity: "3", open_package_key: OPEN_KEY },
      ],
    }),
    summary: { from: "Κέντρο", to: "Βαν" },
  });
  return { ...entry, ...fields };
}

/** Saves and a cart an older build left: a refused save kept at a time
 * not known, and a waiting save and the cart kept at `at`, or at a time
 * not known when it is null. */
function leaveOlder(at: number | null) {
  keep("inventory.outbox.v3", [
    olderSave({
      body: moveBody({
        to_location_id: 99,
        install_number: 5,
        lines: [
          { item_id: 100, quantity: "8", package_id: 1000 },
          { item_id: 100, quantity: "2", package_id: 1003 },
          { item_id: 101, quantity: "3", open_package_key: KEY },
          { item_id: 103, quantity: "4", package_id: 1004 },
          { item_id: 101, quantity: "x" },
        ],
        used_up: [KEY],
      }),
      what: "Ραδιόφωνο",
      queuedAt: null,
      state: "refused",
      reason: { code: "key_reused" },
    }),
    olderSave({
      body: {
        key: OTHER_KEY,
        kind: "report",
        volunteer_id: 2,
        from_location_id: 10,
        to_location_id: null,
        lines: null,
      },
      what: "Ραδιόφωνο",
      queuedAt: at,
    }),
  ]);
  keep("inventory.cart.v1", {
    ...freshCart(),
    // A kind no build knows.
    kind: "lost",
    volunteerId: 2,
    fromId: 10,
    lines: [
      {
        itemId: 101,
        packages: { 1001: "1" },
        loose: "2",
        open: { [KEY]: { amount: "3", usedUp: true } },
      },
      { itemId: 98, packages: { 1: "1" }, loose: "" },
    ],
    usedAt: at,
  });
}

/** Each part covered: what renders it, and the elements it says. */
const COVERED: Record<string, () => Promise<HTMLElement[]>> = {
  "the registration page, a phrase left blank": async () => {
    render(<RegisterPage />);
    fireEvent.submit(screen.getByRole("button"));
    await screen.findByRole("alert");
    return [document.body];
  },
  "the registration page, sending": async () => {
    const { api } = answering(() => deferred().promise);
    window.history.replaceState(null, "", "/register?code=a-b-c-d-e");
    render(<RegisterPage api={api} />);
    fireEvent.submit(screen.getByRole("button"));
    return [document.body];
  },
  "the registration page, refused": async () => {
    const { api } = answering(() => json({ reason: "unknown" }, 400));
    window.history.replaceState(null, "", "/register?code=a-b-c-d-e");
    render(<RegisterPage api={api} />);
    fireEvent.submit(screen.getByRole("button"));
    await screen.findByRole("alert");
    return [document.body];
  },
  "the registration page, registered": async () => {
    const { api } = answering(() => json({ device_id: 4 }, 201));
    window.history.replaceState(null, "", "/register?code=a-b-c-d-e");
    const outbox = {
      entries: signal([{ state: "waiting" }, { state: "waiting" }]),
      send: async () => {},
    } as unknown as Outbox;
    render(<RegisterPage api={api} outbox={outbox} />);
    fireEvent.submit(screen.getByRole("button"));
    await screen.findByRole("status");
    return [document.body];
  },
  "the scan page, loading": async () => {
    scanPage();
    return [document.body];
  },
  "the scan page, a cart with problems": async () => {
    storeCart({
      ...freshCart(),
      volunteerId: 1,
      fromId: 10,
      toId: 12,
      // Long unused, so it asks whose name it is.
      usedAt: 1,
      lines: [
        { itemId: 100, packages: { 1000: "2", 1002: "1" }, loose: "1.5" },
        {
          itemId: 101,
          packages: { 1001: "1" },
          sizes: { 1001: "300" },
          loose: "",
          open: { [OPEN_KEY]: { amount: "9", whole: false } },
        },
        { itemId: 104, packages: {}, loose: "1" },
      ],
    });
    await loaded();
    fireEvent.submit(screen.getByRole("searchbox").closest("form") as Element);
    await screen.findByRole("list", { hidden: false });
    fireEvent.input(screen.getByRole("searchbox"), {
      target: { value: "ζζζ" },
    });
    // Discard cart, asked to confirm.
    fireEvent.click(screen.getAllByRole("button").at(-1) as HTMLElement);
    await vi.waitFor(() =>
      expect(document.querySelector("button.danger")).not.toBeNull(),
    );
    return [document.body];
  },
  "the scan page, a cart sent": async () => {
    storeCart({
      ...freshCart(),
      volunteerId: 1,
      fromId: 10,
      toId: 12,
      lines: [{ itemId: 100, packages: { 1000: "2" }, loose: "" }],
    });
    await loaded();
    fireEvent.submit(screen.getByRole("searchbox").closest("form") as Element);
    await screen.findByRole("status");
    fireEvent.input(screen.getByRole("searchbox"), { target: { value: "Ρα" } });
    return [document.body];
  },
  "the scan page, each kind": async () => {
    storeCart({ ...freshCart(), kind: "install", volunteerId: 1 });
    await loaded();
    fireEvent.click(screen.getAllByRole("radio")[3] as HTMLElement);
    return [document.body];
  },
  "the scan page, a place's label read": async () => {
    storeCart({ ...freshCart(), kind: "move", volunteerId: 1 });
    await loaded();
    const find = screen.getByRole("searchbox");
    fireEvent.input(find, { target: { value: "S4HELF" } });
    fireEvent.keyDown(find, { key: "Enter" });
    await screen.findByRole("group", { name: "Κέντρο" });
    return [document.body];
  },
  "the scan page, unregistered": async () => {
    scanPage(403);
    await screen.findByRole("alert");
    return [document.body];
  },
  "the scan page, the catalogue not loaded": async () => {
    scanPage(500);
    await screen.findByRole("alert");
    return [document.body];
  },
  "the scan page, too old, a save held": async () => {
    storeOutbox([entry(0, { reason: { code: "must_upgrade" } }), entry(1)]);
    const outbox = await loaded();
    outbox.builds.learn("too_old");
    await screen.findByRole("alert");
    expect(await screen.findAllByText(say(HELD))).toHaveLength(2);
    return [document.body];
  },
  "the outbox's list, each reason": async () => {
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    const recorded = {
      by: "elsewhere",
      transactionId: 9,
      batch: {
        ...entry(0).body,
        key: OTHER_KEY,
        lines: [
          { item_id: 100, quantity: "8", package_id: 1000, package_size: "4" },
          { item_id: 101, quantity: "5" },
        ],
      },
    };
    const reasons = [
      null,
      { code: "no_answer" },
      { code: "unavailable" },
      { code: "unexpected", status: 500, tries: 1 },
      { code: "unexpected", status: 500, tries: 6 },
      { code: "must_upgrade" },
    ];
    const entries = [
      ...reasons.map((reason, n) => entry(n, { reason })),
      entry(10, {
        waitingFor: "registration",
        reason: { code: "not_registered", why: "unregistered" },
      }),
      entry(11, { state: "refused", reason: { code: "key_reused" } }),
      entry(12, {
        state: "refused",
        reason: { code: "recorded_elsewhere" },
      }),
      {
        ...entry(13, {
          state: "refused",
          reason: { code: "recorded_elsewhere", recorded },
        }),
        body: {
          ...recorded.batch,
          lines: [
            {
              item_id: 100,
              quantity: "4",
              package_id: 1000,
              package_size: "4",
            },
            { item_id: 101, quantity: "9" },
            { item_id: 101, quantity: "2", open_package_key: OPEN_KEY },
          ],
          used_up: [OPEN_KEY],
          changed_from: recorded.batch.lines,
        },
        putBack: true,
      },
      entry(14, {
        state: "refused",
        reason: {
          code: "refused",
          problems: [
            { names: { of: "line", n: 1 }, kind: "not_whole" },
            { names: { of: "used_up", n: 1 }, kind: "open_package_closed" },
            { names: { of: "lengths", n: 1 }, kind: "made_up" },
            { names: null, kind: "package_size_without_package" },
          ],
        },
      }),
      entry(15, { state: "saved", transactionId: 7 }),
    ];
    storeOutbox(entries);
    storeCart({
      ...freshCart(),
      volunteerId: 1,
      lines: [{ itemId: 100, packages: {}, loose: "1" }],
    });
    await loaded();
    await screen.findAllByRole("heading", { level: 3 });
    return [document.body];
  },
  "what older builds left": async () => {
    leaveOlder(null);
    await loaded();
    return [document.body];
  },
  "what older builds left, with when it was kept": async () => {
    // The date and time are in the device's locale, not the page's, so
    // in digits here.
    vi.spyOn(Date.prototype, "toLocaleString").mockReturnValue(
      "14.11.2026 09:30",
    );
    leaveOlder(1);
    await loaded();
    return [document.body];
  },
  "the camera": async () => {
    const open = async () => ({
      read: async () => [],
      torch: async () => {
        throw new Error("no torch");
      },
      stop: () => {},
    });
    render(<Scanner onCode={() => "Κουτί"} onDone={() => {}} open={open} />);
    const [torch] = await screen.findAllByRole("button", { pressed: false });
    const status = screen.getByRole("status");
    const pointing = status.textContent;
    fireEvent.click(torch as HTMLElement);
    await vi.waitFor(() => expect(status.textContent).not.toBe(pointing));
    return [document.body];
  },
  "the camera, refused": async () => {
    for (const refusal of [
      new DecoderUnavailable(),
      new DOMException("no", "NotAllowedError"),
      new Error("none"),
    ]) {
      render(
        <Scanner
          onCode={() => ""}
          onDone={() => {}}
          open={() => Promise.reject(refusal)}
        />,
      );
    }
    await new Promise((settled) => setTimeout(settled, 0));
    return [document.body];
  },
  "an open package's card, as each cut ends it": async () => {
    const open = { ...OPEN, left: 20_000 };
    for (const [item, amount] of [
      [cable, "20"],
      [cable, "10"],
      [cable, "30"],
      [radio, "20"],
    ] as const) {
      render(
        <OpenPackageCard
          open={open}
          item={item}
          taken={{ amount, whole: false }}
          moving={true}
          onTake={() => {}}
        />,
      );
    }
    render(
      <OpenPackageCard
        open={{ ...open, left: 0 }}
        item={cable}
        taken={{ amount: "", whole: true, now: "" }}
        moving={true}
        onTake={() => {}}
      />,
    );
    return [document.body];
  },
  "an administrator's page, retiring with changes unsaved": async () => {
    document.body.innerHTML = `<form><input name="n"></form>
      <form id="retire" data-retire></form>`;
    guardRetire(document);
    (document.querySelector("input") as HTMLInputElement).value = "Κ";
    (document.getElementById("retire") as HTMLFormElement).requestSubmit();
    return [document.querySelector("dialog") as HTMLElement];
  },
  "an administrator's page, suggestions": async () => {
    document.body.innerHTML = `<input data-suggest="/admin/items/suggest">`;
    suggest(document.querySelector("input") as HTMLInputElement, {
      go: () => {},
    });
    return [document.body];
  },
};

/** The words an element and those in it say, read or spoken: their text,
 * and what a screen reader or a tooltip says of them. */
function said(element: HTMLElement): string[] {
  const attributes = ["aria-label", "title", "placeholder", "alt"];
  const spoken = [element, ...element.querySelectorAll("*")].flatMap((each) =>
    attributes.flatMap((name) => each.getAttribute(name) ?? []),
  );
  return [element.textContent ?? "", ...spoken];
}

test.each(Object.keys(COVERED))(
  "%s says nothing in plain English",
  async (part) => {
    speak("pseudo", pseudo);
    const shown = (await COVERED[part]?.()) ?? [];
    expect(shown.length).toBeGreaterThan(0);
    const english = shown
      .flatMap(said)
      .flatMap((words) => words.match(/[A-Za-z]{2,}/g) ?? [])
      .filter((word) => !DATA.has(word));
    expect(english).toEqual([]);
  },
);
