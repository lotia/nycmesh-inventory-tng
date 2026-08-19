/**
 * What a code's outcome says, for the cases the deep link's own tests do not
 * reach. Written once and used by every way a code arrives, so a superseded
 * sticker has to read sensibly whichever of them found it.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { OutcomeAlert } from "./outcome";

describe("a code read off a sticker that has been replaced", () => {
  it("still sets the location, and says which sticker to reprint", () => {
    render(<OutcomeAlert outcome={{ applied: "location", revoked: true }} onClose={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Location set. That sticker has been replaced — the one on the wall should be reprinted.",
    );
  });
});
