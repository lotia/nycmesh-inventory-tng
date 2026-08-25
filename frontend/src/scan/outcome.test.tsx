/**
 * What a code's outcome says, for the cases the deep link's own tests do not
 * reach. Written once and used by every way a code arrives, so a superseded
 * sticker has to read sensibly whichever of them found it.
 *
 * And what an administrator may do about the sticker while it is on the screen,
 * which is the only moment a label is: decision 0014 point 1.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ADMINISTRATOR,
  renderScreen,
  STALE_ADMINISTRATOR,
  stubSession,
  theWrite,
  VOLUNTEER,
  writes,
} from "../testHarness";
import { OutcomeAlert } from "./outcome";
import { PACKET } from "./testFixtures";

const ADDED = {
  applied: "item",
  code: PACKET.code,
  name: "Zip Ties Reusable",
  quantity: 100,
  revoked: false,
} as const;

/** Everything but the label write, which the caller decides. */
function serving(write: () => Response = () => new Response("{}", { status: 200 })) {
  stubSession(ADMINISTRATOR, (async (_path: string, init?: RequestInit) => {
    return init?.method !== undefined && init.method !== "GET"
      ? write()
      : new Response("{}", { status: 200 });
  }) as unknown as typeof fetch);
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a code read off a sticker that has been replaced", () => {
  it("still sets the location, and says which sticker to reprint", () => {
    serving();
    renderScreen(
      <OutcomeAlert
        outcome={{ applied: "location", code: "5RJ9T4HB2K", revoked: true }}
        onClose={vi.fn()}
        onRevoked={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Location set. That sticker has been replaced — the one on the wall should be reprinted.",
    );
  });
});

describe("revoking the sticker that is on the screen", () => {
  it("is not offered to a volunteer", async () => {
    stubSession(
      VOLUNTEER,
      (async () => new Response("{}", { status: 200 })) as unknown as typeof fetch,
    );
    renderScreen(<OutcomeAlert outcome={ADDED} onClose={vi.fn()} onRevoked={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /revoke this label/i })).not.toBeInTheDocument();
  });

  it("is not offered to a session the server wants a second look at", async () => {
    // The same rule the item list is held to: the capability is about now,
    // so the control goes rather than failing when it is pressed.
    stubSession(
      STALE_ADMINISTRATOR,
      (async () => new Response("{}", { status: 200 })) as unknown as typeof fetch,
    );
    renderScreen(<OutcomeAlert outcome={ADDED} onClose={vi.fn()} onRevoked={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /revoke this label/i })).not.toBeInTheDocument();
  });

  it("asks before it does it, naming the code on the sticker", async () => {
    serving();
    renderScreen(<OutcomeAlert outcome={ADDED} onClose={vi.fn()} onRevoked={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /revoke this label/i }));

    const asking = await screen.findByRole("dialog");
    expect(asking).toHaveTextContent(PACKET.code);
    // Nothing is written by asking: a scan is one tap, and this must not be.
    expect(writes()).toHaveLength(0);

    fireEvent.click(within(asking).getByRole("button", { name: /keep it/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(writes()).toHaveLength(0);

    // And pressing Escape is the same answer, which is the way most people
    // put a dialog away on a phone.
    fireEvent.click(screen.getByRole("button", { name: /revoke this label/i }));
    fireEvent.keyDown(await screen.findByRole("dialog"), { key: "Escape", code: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(writes()).toHaveLength(0);
  });

  it("patches revoked rather than deleting, because the ledger refers to it", async () => {
    serving();
    renderScreen(<OutcomeAlert outcome={ADDED} onClose={vi.fn()} onRevoked={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /revoke this label/i }));
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: /revoke it/i }),
    );

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(theWrite()).toEqual({
      path: `/api/labels/${PACKET.code}`,
      method: "PATCH",
      // A boolean, not a moment: the server owns the clock. LabelSerializer.
      body: { revoked: true },
    });
  });

  it("leaves the sticker reading as revoked rather than as fine", async () => {
    // Somebody is holding it. The line that said the scan was fine has to stop
    // saying so, and start saying what to do about it.
    serving();
    function Screen() {
      const [revoked, setRevoked] = useState(false);
      return (
        <OutcomeAlert
          outcome={{ ...ADDED, revoked }}
          onClose={vi.fn()}
          onRevoked={() => setRevoked(true)}
        />
      );
    }
    renderScreen(<Screen />);
    fireEvent.click(await screen.findByRole("button", { name: /revoke this label/i }));
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: /revoke it/i }),
    );

    expect(await screen.findByText(/that sticker has been replaced/i)).toBeInTheDocument();
    // And the control is gone: there is nothing left to give up.
    expect(screen.queryByRole("button", { name: /revoke this label/i })).not.toBeInTheDocument();
  });

  it("offers the way back when the server wants a second look", async () => {
    serving(
      () =>
        new Response(
          JSON.stringify({
            detail: "Sign in again to make this change.",
            code: "reauthentication_required",
          }),
          { status: 403 },
        ),
    );
    renderScreen(<OutcomeAlert outcome={ADDED} onClose={vi.fn()} onRevoked={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /revoke this label/i }));
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: /revoke it/i }),
    );

    expect(await screen.findByRole("link", { name: /sign in again/i })).toHaveAttribute(
      "href",
      expect.stringContaining("/accounts/reauthenticate/"),
    );
    // The sticker is untouched and the line still says the scan landed. By
    // text rather than by role: the prompt is an alert as well.
    expect(screen.getByText("Added 100 × Zip Ties Reusable.")).toBeInTheDocument();

    // The prompt is dismissible, leaving the question still asked.
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: /close/i }));
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: /sign in again/i })).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("is not offered on a sticker that has already been replaced", async () => {
    serving();
    renderScreen(
      <OutcomeAlert outcome={{ ...ADDED, revoked: true }} onClose={vi.fn()} onRevoked={vi.fn()} />,
    );

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /revoke this label/i })).not.toBeInTheDocument();
  });
});
