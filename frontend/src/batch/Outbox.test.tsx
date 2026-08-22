/**
 * What the volunteer sees while the device is holding their work.
 *
 * The queue itself is tested in outbox.test.ts. What is here is the part that
 * decides whether any of it is any use: that a held batch is visible, that it
 * is named, that it goes on its own when the network comes back, and that a
 * batch the server refused says so instead of waiting forever.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Outbox } from "./Outbox";
import { outbox, queueBatch } from "./outbox";
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

const panel = () => screen.getByRole("region", { name: /not yet sent/i });

beforeEach(() => {
  nothingQueued();
  stubFetch();
  // The panel sends on mount, so every test here starts with a network that
  // is not there and says when it comes back.
  fetching.mockImplementation(nothing);
});

afterEach(forgetWhatWasStubbed);

it("says nothing when there is nothing to say", () => {
  render(<Outbox />);

  expect(screen.queryByRole("region")).not.toBeInTheDocument();
});

it("names the batch it is holding", async () => {
  queueBatch(batch("key-1", "LiteBeam and Cat6 Outdoor"));
  render(<Outbox />);

  expect(await within(panel()).findByText(/LiteBeam and Cat6 Outdoor/)).toBeInTheDocument();
  expect(panel()).toHaveTextContent(/waiting to send/i);
});

describe("getting it sent", () => {
  it("tries as soon as the app opens", async () => {
    queueBatch(batch());
    fetching.mockResolvedValue(recorded());

    render(<Outbox />);

    expect(await screen.findByText(/Recorded\./)).toBeInTheDocument();
  });

  it("tries again when the browser says the network is back", async () => {
    queueBatch(batch());
    render(<Outbox />);
    await waitFor(() => expect(fetching).toHaveBeenCalledTimes(1));
    fetching.mockResolvedValue(recorded());

    window.dispatchEvent(new Event("online"));

    expect(await screen.findByText(/Recorded\./)).toBeInTheDocument();
  });

  it("tries again when the volunteer asks, because the browser can be wrong", async () => {
    queueBatch(batch());
    render(<Outbox />);
    await waitFor(() => expect(fetching).toHaveBeenCalledTimes(1));
    fetching.mockResolvedValue(recorded());

    fireEvent.click(screen.getByRole("button", { name: /send now/i }));

    expect(await screen.findByText(/Recorded\./)).toBeInTheDocument();
  });

  it("stops offering to send once nothing is waiting", async () => {
    queueBatch(batch());
    fetching.mockResolvedValue(recorded());
    render(<Outbox />);

    await screen.findByText(/Recorded\./);

    expect(screen.queryByRole("button", { name: /send now/i })).not.toBeInTheDocument();
  });
});

describe("news the volunteer has read", () => {
  it("is dismissed, and does not come back", async () => {
    queueBatch(batch());
    fetching.mockResolvedValue(recorded());
    render(<Outbox />);
    await screen.findByText(/Recorded\./);

    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));

    await waitFor(() => expect(screen.queryByRole("region")).not.toBeInTheDocument());
    expect(outbox()).toEqual([]);
  });
});

describe("a batch the server refused", () => {
  it("says so, and says it will not be tried again", async () => {
    queueBatch(batch());
    fetching.mockResolvedValue(answered(400, "Nothing was saved."));

    render(<Outbox />);

    expect(await screen.findByText(/Nothing was saved\./)).toBeInTheDocument();
    expect(panel()).toHaveTextContent(/will not be sent again/i);
    // Thrown away rather than dismissed: this work never reached the ledger,
    // and the word has to say which of the two happened.
    expect(screen.getByRole("button", { name: /discard/i })).toBeInTheDocument();
  });
});
