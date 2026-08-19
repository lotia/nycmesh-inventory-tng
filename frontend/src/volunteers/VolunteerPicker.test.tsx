/**
 * The picker, exercised as the flow it is: search, find nobody, add yourself.
 *
 * What is asserted throughout is the order — matches before the create option
 * — because that order is the whole reason this endpoint exists.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { page } from "../api/testFixtures";
import type { Volunteer } from "../api/types";
import { CartProvider } from "../cart/CartProvider";
import { olivia, sean } from "./testFixtures";
import { VolunteerPicker } from "./VolunteerPicker";

/** A list endpoint that searches, and a create that answers however asked. */
function api(known: Volunteer[], onCreate?: () => Response): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return onCreate?.() ?? new Response(JSON.stringify(olivia), { status: 201 });
      }
      const search = new URL(path, "http://test").searchParams.get("search") ?? "";
      const matching = known.filter((v) =>
        v.display_name.toLowerCase().includes(search.toLowerCase()),
      );
      return new Response(JSON.stringify(page(...matching)), { status: 200 });
    }),
  );
}

function show() {
  return render(
    <CartProvider>
      <VolunteerPicker />
    </CartProvider>,
  );
}

function type(value: string): void {
  fireEvent.change(screen.getByRole("textbox", { name: /who are you/i }), { target: { value } });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("finding somebody who is already there", () => {
  it("shows near-matches as they are typed", async () => {
    api([sean, olivia]);
    show();
    type("sean");
    expect(await screen.findByRole("button", { name: /sean mcginnis/i })).toBeInTheDocument();
  });

  it("offers no way to add anybody before something has been typed", async () => {
    api([sean]);
    show();
    await screen.findByRole("button", { name: /sean mcginnis/i });
    expect(screen.queryByRole("button", { name: /^add /i })).not.toBeInTheDocument();
  });

  it("offers the matches before it offers to add a name", async () => {
    api([sean]);
    show();
    type("sean");
    const match = await screen.findByRole("button", { name: /sean mcginnis/i });
    const add = await screen.findByRole("button", { name: /not listed/i });
    expect(match.compareDocumentPosition(add) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("works as whoever was picked", async () => {
    api([sean]);
    show();
    type("sean");
    fireEvent.click(await screen.findByRole("button", { name: /sean mcginnis/i }));
    expect(await screen.findByText(/working as/i)).toHaveTextContent("Sean McGinnis");
  });

  it("still knows who you are after a reload", async () => {
    api([sean]);
    const first = show();
    type("sean");
    fireEvent.click(await screen.findByRole("button", { name: /sean mcginnis/i }));
    await screen.findByText(/working as/i);
    first.unmount();

    show();
    expect(await screen.findByText(/working as/i)).toHaveTextContent("Sean McGinnis");
  });

  it("can hand the phone to somebody else", async () => {
    api([sean]);
    show();
    type("sean");
    fireEvent.click(await screen.findByRole("button", { name: /sean mcginnis/i }));
    fireEvent.click(await screen.findByRole("button", { name: /not you/i }));
    expect(await screen.findByRole("textbox", { name: /who are you/i })).toBeInTheDocument();
  });
});

describe("adding yourself when the search found nobody", () => {
  it("offers to add the name that matched nothing", async () => {
    api([]);
    show();
    type("Olivia");
    expect(await screen.findByRole("button", { name: "Add Olivia" })).toBeInTheDocument();
  });

  it("makes the new volunteer the actor straight away", async () => {
    api([]);
    show();
    type("Olivia");
    fireEvent.click(await screen.findByRole("button", { name: "Add Olivia" }));
    expect(await screen.findByText(/working as/i)).toHaveTextContent("Olivia");
  });

  it("does not offer to add anybody when the search itself failed", async () => {
    // A failed search shows nothing, and nothing looks exactly like nobody:
    // offering "Add Olivia" because the network dropped is how this screen
    // produces the duplicate it exists to prevent, in the basement it is for.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "Nope." }), { status: 500 })),
    );
    show();
    type("Olivia");
    expect(await screen.findByRole("alert")).toHaveTextContent(/nope|could not be reached/i);
    expect(screen.queryByRole("button", { name: /add olivia/i })).not.toBeInTheDocument();
  });

  it("asks for a name when what was typed is a Slack ID, not just an address", async () => {
    api([]);
    show();
    type("U024BE7LH");
    expect(await screen.findByRole("textbox", { name: /and your name/i })).toBeInTheDocument();
  });

  it("registers a Slack ID as a Slack ID", async () => {
    api([]);
    show();
    type("U024BE7LH");
    fireEvent.change(await screen.findByRole("textbox", { name: /and your name/i }), {
      target: { value: "Sean McGinnis" },
    });
    fireEvent.click(screen.getByRole("button", { name: /add u024be7lh/i }));
    await waitFor(() => expect(screen.getByText(/working as/i)).toBeInTheDocument());
    const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls as [
      string,
      RequestInit,
    ][];
    const [, init] = calls.filter(([, options]) => options?.method === "POST").at(-1) as [
      string,
      RequestInit,
    ];
    expect(JSON.parse(String(init.body))).toEqual({
      display_name: "Sean McGinnis",
      slack_id: "U024BE7LH",
    });
  });

  it("asks for a name when what was typed is an address", async () => {
    api([]);
    show();
    type("sean@example.org");
    expect(await screen.findByRole("textbox", { name: /and your name/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /add sean@example.org/i })).toBeDisabled();
  });

  it("registers an address as an address, not as somebody's name", async () => {
    api([]);
    show();
    type("sean@example.org");
    fireEvent.change(await screen.findByRole("textbox", { name: /and your name/i }), {
      target: { value: "Sean McGinnis" },
    });
    fireEvent.click(screen.getByRole("button", { name: /add sean@example.org/i }));
    await waitFor(() => expect(screen.getByText(/working as/i)).toBeInTheDocument());
    // The POST, not the list re-read that follows it.
    const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls as [
      string,
      RequestInit,
    ][];
    const [, init] = calls.filter(([, options]) => options?.method === "POST").at(-1) as [
      string,
      RequestInit,
    ];
    expect(JSON.parse(String(init.body))).toEqual({
      display_name: "Sean McGinnis",
      email: "sean@example.org",
    });
  });

  it("asks for no second field when a name was typed", async () => {
    api([]);
    show();
    type("Olivia");
    await screen.findByRole("button", { name: "Add Olivia" });
    expect(screen.queryByRole("textbox", { name: /and your name/i })).not.toBeInTheDocument();
  });

  it("offers the survivor when the name clashes with a merged record", async () => {
    api(
      [],
      () =>
        new Response(
          JSON.stringify({
            detail:
              "That email address is already recorded, on a duplicate record since merged into Sean McGinnis.",
            code: "volunteer_merged",
            field: "email",
            volunteer: sean,
            selectable: true,
          }),
          { status: 409 },
        ),
    );
    show();
    type("Sean");
    fireEvent.click(await screen.findByRole("button", { name: "Add Sean" }));
    fireEvent.click(await screen.findByRole("button", { name: /continue as sean mcginnis/i }));
    expect(await screen.findByText(/working as/i)).toHaveTextContent("Sean McGinnis");
  });

  it("says what happened without offering a record nobody may pick", async () => {
    api(
      [],
      () =>
        new Response(
          JSON.stringify({
            detail: "That email address is already recorded, on a record that has been retired.",
            code: "volunteer_inactive",
            field: "email",
            volunteer: sean,
            selectable: false,
          }),
          { status: 409 },
        ),
    );
    show();
    type("Sean");
    fireEvent.click(await screen.findByRole("button", { name: "Add Sean" }));
    expect(await screen.findByText(/has been retired/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /continue as/i })).not.toBeInTheDocument();
  });

  it("shows an ordinary rejection as an error rather than as a conflict", async () => {
    api(
      [],
      () => new Response(JSON.stringify({ detail: "Too many submissions." }), { status: 429 }),
    );
    show();
    type("Olivia");
    fireEvent.click(await screen.findByRole("button", { name: "Add Olivia" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Too many submissions."),
    );
  });
});
