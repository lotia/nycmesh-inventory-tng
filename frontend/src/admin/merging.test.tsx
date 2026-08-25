/**
 * Merging two volunteers, and what the 409 of decision 0015 is allowed to say.
 *
 * Two claims are under test and the second is the one worth having. The first
 * is ordinary: an administrator can say that two of the people on screen are
 * one person, and a volunteer cannot. The second is that the conflict body
 * carries more about a record the pick-list refuses to show than this screen
 * puts on it — `VolunteerConflict` is the argument, and the last case here is
 * what stops the obvious widening from happening by accident later.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { answering, page } from "../api/testFixtures";
import type { Volunteer, VolunteerConflict } from "../api/types";
import {
  ADMINISTRATOR,
  pickOption,
  renderScreen,
  STALE_ADMINISTRATOR,
  stubSession,
  theWrite,
  VOLUNTEER,
  writes,
} from "../testHarness";
import { olivia, sean } from "../volunteers/testFixtures";
import { VolunteerPicker } from "../volunteers/VolunteerPicker";

/** A second Sean, which is the whole situation a merge is for. */
const OTHER_SEAN: Volunteer = {
  id: 12,
  display_name: "Sean Mcginnis",
  email: "s.mcginnis@example.org",
  slack_id: "U024BE7LH",
};

/**
 * The pick-list as it answers a search, plus whatever a write should say.
 *
 * `known` rather than a fixed page, because half of these are about a search
 * finding two people and the other half about it finding one.
 */
function pickList(
  session: unknown,
  known: Volunteer[],
  write: (method: string) => Response = () => answering(sean),
) {
  stubSession(session, (async (path: string, init?: RequestInit) => {
    if (init?.method !== undefined && init.method !== "GET") {
      return write(init.method);
    }
    const search = new URL(path, "http://test").searchParams.get("search") ?? "";
    return answering(
      page(...known.filter((one) => one.display_name.toLowerCase().includes(search.toLowerCase()))),
    );
  }) as unknown as typeof fetch);
}

function type(value: string): void {
  fireEvent.change(screen.getByRole("textbox", { name: /who are you/i }), { target: { value } });
}

/** The rows the search turned up, whatever they are called. */
function matchesShown(): HTMLElement[] {
  return within(screen.getByRole("list", { name: /volunteers/i })).queryAllByRole("button");
}

/**
 * Wait for the pair.
 *
 * By count rather than by name, because the two names differ only in a capital
 * letter -- which is the whole situation a merge is for, and which makes
 * `findByRole` with either of them ambiguous.
 */
async function twoSeans(): Promise<void> {
  await waitFor(() => expect(matchesShown()).toHaveLength(2));
}

/**
 * The address the searcher types, which is not one the named record shows.
 *
 * Deliberately not `OTHER_SEAN.email`: what the searcher typed is on their own
 * screen whatever happens, so a leak test written against it could never fail.
 * They know this address; the identifiers on the record they collided with are
 * the ones they do not.
 */
const TYPED = "sean@example.org";

/** Open the merge dialog against the pair the search turned up. */
async function merging(): Promise<HTMLElement> {
  renderScreen(<VolunteerPicker />);
  type("sean");
  await twoSeans();
  fireEvent.click(screen.getByRole("button", { name: /two of these/i }));
  return screen.findByRole("dialog");
}

/** Answer one of the dialog's two ends, which is what the pair is about. */
const pick = (dialog: HTMLElement, end: RegExp, who: Volunteer) =>
  pickOption(within(dialog), end, who.display_name);

const STOP = /stop offering this one/i;
const KEEP = /keep this one/i;

/** The 409 body decision 0015 point 4 describes, in either of its two codes. */
function conflict(selectable: boolean): VolunteerConflict {
  return {
    detail: selectable
      ? `That email address is already recorded, on a duplicate record since merged into ${OTHER_SEAN.display_name}. Continue as them rather than adding yourself again.`
      : `That email address is already recorded, on ${OTHER_SEAN.display_name}'s record. That record has been retired, and an administrator can restore it.`,
    code: selectable ? "volunteer_merged" : "volunteer_inactive",
    field: "email",
    volunteer: OTHER_SEAN,
    selectable,
  };
}

/** Provoke the 409 the way a person does: type an address nobody is offered. */
async function clashing(session: unknown, body: VolunteerConflict): Promise<void> {
  // The POST is what is refused. A PATCH from here is the restore, and it
  // answers with the record as it now stands -- offered again.
  pickList(session, [], (method) =>
    method === "POST" ? answering(body, 409) : answering({ ...OTHER_SEAN, active: true }),
  );
  renderScreen(<VolunteerPicker />);
  type(TYPED);
  fireEvent.change(await screen.findByRole("textbox", { name: /and your name/i }), {
    target: { value: "Sean" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^add /i }));
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("saying that two of these are one person", () => {
  it("is not offered to a volunteer", async () => {
    pickList(VOLUNTEER, [sean, OTHER_SEAN]);
    renderScreen(<VolunteerPicker />);
    type("sean");

    await twoSeans();
    expect(
      screen.queryByRole("button", { name: /two of these are the same person/i }),
    ).not.toBeInTheDocument();
  });

  it("is not offered to a session the server wants a second look at", async () => {
    pickList(STALE_ADMINISTRATOR, [sean, OTHER_SEAN]);
    renderScreen(<VolunteerPicker />);
    type("sean");

    await twoSeans();
    expect(
      screen.queryByRole("button", { name: /two of these are the same person/i }),
    ).not.toBeInTheDocument();
  });

  it("is not offered before anything has been typed", async () => {
    // VolunteerPicker's gate says what an empty search actually returns.
    pickList(ADMINISTRATOR, [sean, OTHER_SEAN]);
    renderScreen(<VolunteerPicker />);

    await twoSeans();
    expect(
      screen.queryByRole("button", { name: /two of these are the same person/i }),
    ).not.toBeInTheDocument();
  });

  it("is not offered against one person, because one is not a duplicate", async () => {
    pickList(ADMINISTRATOR, [sean, olivia]);
    renderScreen(<VolunteerPicker />);
    type("olivia");

    await waitFor(() => expect(matchesShown()).toHaveLength(1));
    expect(
      screen.queryByRole("button", { name: /two of these are the same person/i }),
    ).not.toBeInTheDocument();
  });

  it("points the duplicate at the survivor, which is the direction the model takes", async () => {
    pickList(ADMINISTRATOR, [sean, OTHER_SEAN]);
    const dialog = await merging();
    await pick(dialog, STOP, OTHER_SEAN);
    await pick(dialog, KEEP, sean);
    fireEvent.click(within(dialog).getByRole("button", { name: /^merge$/i }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // The edit is to the duplicate and it names the survivor: `merged_into`
    // points forward, and both directions are legal edits, so which way round
    // is not something the database could have caught.
    expect(theWrite()).toEqual({
      path: `/api/volunteers/${OTHER_SEAN.id}`,
      method: "PATCH",
      body: { merged_into: sean.id },
    });
  });

  it("will not merge somebody into themselves", async () => {
    pickList(ADMINISTRATOR, [sean, OTHER_SEAN]);
    const dialog = await merging();
    await pick(dialog, STOP, sean);
    await pick(dialog, KEEP, sean);

    expect(within(dialog).getByRole("button", { name: /^merge$/i })).toBeDisabled();
    expect(writes()).toHaveLength(0);
  });

  it("says what the server refused, in the words the server used", async () => {
    // The refusal `guides/administrator.md` warns an administrator about, and
    // the one that arrives as a non-field error rather than against a box.
    const held =
      "Sean Mcginnis still holds the custody location 'Sean'. Move the stock recorded there and retire it, or hand it to somebody else, before merging or retiring them.";
    pickList(ADMINISTRATOR, [sean, OTHER_SEAN], () => answering({ non_field_errors: [held] }, 400));
    const dialog = await merging();
    await pick(dialog, STOP, OTHER_SEAN);
    await pick(dialog, KEEP, sean);
    fireEvent.click(within(dialog).getByRole("button", { name: /^merge$/i }));

    expect(await screen.findByText(/still holds the custody location/i)).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

describe("the conflict decision 0015 answers a self-registration with", () => {
  it("says the server's own sentence, to anybody", async () => {
    await clashing(VOLUNTEER, conflict(true));

    expect(await screen.findByText(conflict(true).detail)).toBeInTheDocument();
    // The way out 0015 exists to provide is not an administrator's: it is the
    // volunteer's, and it is why the endpoint answers 409 rather than 400.
    expect(
      screen.getByRole("button", { name: `Continue as ${OTHER_SEAN.display_name}` }),
    ).toBeInTheDocument();
  });

  it("does not put the named record's identifiers on the screen, for a volunteer", async () => {
    // VolunteerConflict says which fields the 409 carries and why none of
    // them is drawn. This is that holding for the two the searcher never knew.
    await clashing(VOLUNTEER, conflict(false));

    expect(await screen.findByText(conflict(false).detail)).toBeInTheDocument();
    expect(screen.queryByText(OTHER_SEAN.email as string)).not.toBeInTheDocument();
    expect(screen.queryByText(OTHER_SEAN.slack_id as string)).not.toBeInTheDocument();
  });

  it("does not put them there for an administrator either, because the sentence is not gated", async () => {
    // What `merge_volunteers` decides is which button is drawn, never how much
    // of the record is said. Signing in does not widen a disclosure.
    await clashing(ADMINISTRATOR, conflict(false));

    expect(await screen.findByText(conflict(false).detail)).toBeInTheDocument();
    expect(screen.queryByText(OTHER_SEAN.email as string)).not.toBeInTheDocument();
    expect(screen.queryByText(OTHER_SEAN.slack_id as string)).not.toBeInTheDocument();
  });

  it("offers a volunteer nothing to press when nothing can be picked", async () => {
    await clashing(VOLUNTEER, conflict(false));

    expect(await screen.findByText(/an administrator can restore it/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /continue as/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /restore/i })).not.toBeInTheDocument();
  });

  it("offers an administrator the act the sentence names", async () => {
    // "an administrator can restore it", said to an administrator who until
    // now had to go and open Django's admin to do it.
    await clashing(ADMINISTRATOR, conflict(false));

    fireEvent.click(
      await screen.findByRole("button", { name: `Restore ${OTHER_SEAN.display_name}` }),
    );

    // VolunteerConflict says why the body is that field and no other.
    await waitFor(() => expect(writes("PATCH")).toHaveLength(1));
    expect(writes("PATCH")[0]).toEqual({
      path: `/api/volunteers/${OTHER_SEAN.id}`,
      method: "PATCH",
      body: { active: true },
    });
    // And then they are who this batch is attributed to.
    expect(await screen.findByText(/working as/i)).toHaveTextContent(OTHER_SEAN.display_name);
  });
});
