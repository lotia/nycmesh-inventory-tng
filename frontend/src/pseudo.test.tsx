/**
 * What has moved into the catalogue, rendered in the pseudo-locale, which
 * accents every letter: a word still in plain English was not read through
 * it (docs/decisions/0022). Each part of moving the page's words adds what
 * it moved to COVERED, until the whole of each page is there.
 */
import { signal } from "@preact/signals";
import { fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, expect, test } from "vitest";
import { speak } from "./i18n";
import { messages as english } from "./locales/en.po";
import { messages as pseudo } from "./locales/pseudo.po";
import type { Outbox } from "./outbox";
import { RegisterPage } from "./RegisterPage";
import { answering, json } from "./test-fetch";

afterEach(() => speak("en", english));

/** An outbox holding batches that wait for this browser to register. */
const waiting = (count: number) =>
  ({
    entries: signal(
      Array.from({ length: count }, () => ({ state: "waiting" })),
    ),
    send: async () => {},
  }) as unknown as Outbox;

/** Each part covered: what renders it, and the elements it says. */
const COVERED: Record<string, () => Promise<HTMLElement[]>> = {
  "the registration page, registered": async () => {
    const { api } = answering(() => json({ device_id: 4 }, 201));
    window.history.replaceState(null, "", "/register?code=a-b-c-d-e");
    render(<RegisterPage api={api} outbox={waiting(2)} />);
    fireEvent.submit(screen.getByRole("button"));
    const status = await screen.findByRole("status");
    return [status, status.nextElementSibling as HTMLElement];
  },
};

test.each(Object.keys(COVERED))(
  "%s says nothing in plain English",
  async (part) => {
    speak("pseudo", pseudo);
    const said = (await COVERED[part]?.()) ?? [];
    expect(said.length).toBeGreaterThan(0);
    const english = said.flatMap(
      (element) => element.textContent?.match(/[A-Za-z]{2,}/g) ?? [],
    );
    expect(english).toEqual([]);
  },
);
