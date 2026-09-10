/**
 * The way in, and the three answers it has to tell apart.
 *
 * `inventory-tng-qci1`: the app had no sign-in control at all, and the three
 * states below rendered identically -- never signed in, signed in without the
 * staff flag, and a session gone stale. A person told they were an
 * administrator saw an app with no administrative controls and no way to find
 * out which of the three they were in.
 */
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { answering, page } from "../api/testFixtures";
import {
  ADMINISTRATOR,
  SIGNED_IN,
  STALE_ADMINISTRATOR,
  stubSession,
  VOLUNTEER,
} from "../testHarness";

/** Everything but `/api/me`, which `stubSession` answers. */
const EMPTY = async () => answering(page());

function renderAs(session: unknown) {
  stubSession(session, EMPTY);
  return render(<App />);
}

describe("the way in", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("offers a volunteer a way to sign in, pointed at allauth", async () => {
    renderAs(VOLUNTEER);

    const link = await screen.findByRole("link", { name: /^sign in$/i });
    expect(link).toHaveAttribute("href", expect.stringContaining("/accounts/login/"));
    // A volunteer never signed in, so the sentence about authority is not
    // theirs to read either.
    expect(screen.queryByText(/not an administrator/i)).not.toBeInTheDocument();
  });

  it("comes back to the page it left, so signing in is not a detour", async () => {
    renderAs(VOLUNTEER);

    const link = await screen.findByRole("link", { name: /^sign in$/i });
    expect(link).toHaveAttribute(
      "href",
      expect.stringContaining(`next=${encodeURIComponent(window.location.pathname)}`),
    );
  });

  it("draws nothing at all until the server has answered", () => {
    // No `await`: this is the render before `/api/me` resolves, which is the
    // moment the control must not guess. An administrator meets it on every
    // load, and a Sign in button here would be wrong every time.
    renderAs(ADMINISTRATOR);

    expect(screen.queryByRole("link", { name: /^sign in$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^sign out$/i })).not.toBeInTheDocument();
  });

  it("shows an administrator who they are and a way out, not a way in", async () => {
    renderAs(ADMINISTRATOR);

    expect(await screen.findByText("editor")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^sign out$/i })).toHaveAttribute(
      "href",
      expect.stringContaining("/accounts/logout/"),
    );
    expect(screen.queryByRole("link", { name: /^sign in$/i })).not.toBeInTheDocument();
    // An administrator has authority, so the sentence about not having it is
    // asserted here rather than in a second mount of the same session.
    expect(screen.queryByText(/not an administrator/i)).not.toBeInTheDocument();
  });

  it("does not let the other session control draw from an unsettled answer either", () => {
    // StaleSession asks a different question and is correct here for a reason
    // it does not state: ANONYMOUS carries `administrator: false`, so it
    // renders nothing while the read is in flight. That is safety by a
    // neighbouring default rather than by intent, and it is the same hazard
    // this whole change is about -- so it is pinned here rather than left to
    // whoever next adds a check to that component.
    stubSession(STALE_ADMINISTRATOR, EMPTY);
    render(<App />);

    expect(screen.queryByText(/sign in again to make this change/i)).not.toBeInTheDocument();
  });

  it("tells somebody signed in without authority which of the two they are missing", async () => {
    renderAs(SIGNED_IN);

    expect(await screen.findByText(/not an administrator/i)).toBeInTheDocument();
    expect(screen.getByText(/granted by somebody who already has it/i)).toBeInTheDocument();
  });
});
